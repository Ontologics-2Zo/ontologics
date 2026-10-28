"""Fixed-model OpenAI Responses adapter using only Python's standard library.

No routing or approval tools. Keep one provider per ModelRuntime run. Replay the
exact API output items (including encrypted reasoning) and original call IDs;
never reconstruct a provider's reasoning transcript from assistant text.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import time
from urllib import error, request

from .model import canonical, digest
from .service import DomainError
from .tool_specs import function_tools
from .harness import begin_paid_call
from .paid_budget import COST_ENV_KEYS

MODEL = 'gpt-5.4-mini-2026-03-17'
ENDPOINT = 'https://api.openai.com/v1/responses'
# Fixed-model context window, verified against the official model documentation.
# Reserve the entire window rather than guessing tokens from characters/bytes.
INPUT_TOKEN_RESERVATION_FLOOR = 400_000
_ENV_KEYS = {'OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_TIMEOUT_SECONDS', 'OPENAI_MAX_OUTPUT_TOKENS'}

class ProviderError(DomainError):
    """Safe error code/message only: no HTTP body, request headers, or secrets."""

@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = field(repr=False)
    model: str = MODEL
    timeout_seconds: float = 30
    max_output_tokens: int = 4096

    def __post_init__(self):
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ProviderError('OPENAI_KEY_MISSING', 'Set OPENAI_API_KEY locally; never put it in chat or Git')
        if any(c.isspace() for c in self.api_key) or len(self.api_key) > 1024:
            raise ProviderError('OPENAI_CONFIG_INVALID', 'Invalid API key format')
        if self.model != MODEL:
            raise ProviderError('OPENAI_MODEL_UNSUPPORTED', 'This adapter is verified for the documented fixed snapshot only')
        if not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 60:
            raise ProviderError('OPENAI_CONFIG_INVALID', 'Timeout must be between 1 and 60 seconds')
        if type(self.max_output_tokens) is not int or not 256 <= self.max_output_tokens <= 8192:
            raise ProviderError('OPENAI_CONFIG_INVALID', 'Output token limit must be 256..8192')

    @classmethod
    def from_environment(cls, env_file=None, environ=None):
        values = {}
        if env_file is not None and Path(env_file).exists():
            # Parse assignments as data, never source/execute shell content.
            try:
                content = Path(env_file).read_text(encoding='utf-8')
                if len(content) > 16384: raise ValueError()
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    name, sep, value = line.partition('=')
                    name, value = name.strip(), value.strip()
                    if not sep or name not in _ENV_KEYS | COST_ENV_KEYS | {'DATABASE_URL', 'ONTOLOGICS_DB_SCHEMA', 'POSTGRES_PASSWORD', 'POSTGRES_PORT', 'TEST_DATABASE_URL'}: raise ValueError()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    values[name] = value
            except (OSError, UnicodeError, ValueError):
                raise ProviderError('OPENAI_CONFIG_INVALID', 'Environment file must contain supported KEY=value assignments only') from None
        environment = os.environ if environ is None else environ
        for key in _ENV_KEYS:
            if key in environment: values[key] = environment[key]
        try:
            return cls(api_key=values.get('OPENAI_API_KEY', ''), model=values.get('OPENAI_MODEL', MODEL),
                timeout_seconds=float(values.get('OPENAI_TIMEOUT_SECONDS', '30')),
                max_output_tokens=int(values.get('OPENAI_MAX_OUTPUT_TOKENS', '4096')))
        except (TypeError, ValueError):
            raise ProviderError('OPENAI_CONFIG_INVALID', 'Invalid numeric provider configuration') from None

    def public_config(self):
        return {'provider': 'openai', 'endpoint': ENDPOINT, 'model': self.model,
                'timeout_seconds': self.timeout_seconds, 'max_output_tokens': self.max_output_tokens,
                'reasoning_effort': 'none', 'store': False, 'parallel_tool_calls': False,
                'input_token_reservation_floor': INPUT_TOKEN_RESERVATION_FLOOR}

class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Authorization to a redirected endpoint.

def _safe_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value) else None

class OpenAIProvider:
    def __init__(self, settings, tools, *, opener=None):
        self.settings = settings
        self.tools = function_tools(tools)
        self._tool_names = set(tools)
        self._open = opener or request.build_opener(_NoRedirect()).open
        self._outputs = {}
        self.calls = []  # Safe request IDs/status/token counts, never secret/body/headers.

    def public_config(self):
        return self.settings.public_config()

    def _input(self, messages):
        result, pending = [], []
        for i, message in enumerate(messages):
            role = message['role']
            if role == 'assistant' and 'tool_calls' in message:
                if pending: raise ProviderError('OPENAI_HISTORY_INVALID', 'Missing tool result before next assistant response')
                output = self._outputs.get(digest(messages[:i + 1]))
                if output is None:
                    raise ProviderError('OPENAI_HISTORY_INVALID', 'Provider output history unavailable; use a fresh provider for a fresh run')
                result.extend(output)
                pending = [{'name': item['name'], 'call_id': item['call_id'], 'index': j}
                    for j, item in enumerate(item for item in output if item['type'] == 'function_call')]
            elif role == 'tool':
                if not pending or message.get('name') != pending[0]['name'] or message.get('call_index') != pending[0]['index']:
                    raise ProviderError('OPENAI_HISTORY_INVALID', 'Tool call/result pairing mismatch')
                call = pending.pop(0)
                result.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': message['content']})
            else:
                if pending: raise ProviderError('OPENAI_HISTORY_INVALID', 'Missing tool results')
                if role not in ('system', 'user', 'assistant') or not isinstance(message.get('content'), str):
                    raise ProviderError('OPENAI_HISTORY_INVALID', 'Unsupported history item')
                result.append({'role': role, 'content': message['content']})
        if pending: raise ProviderError('OPENAI_HISTORY_INVALID', 'Missing tool results')
        return result

    def _normalize(self, data):
        if not isinstance(data, dict) or data.get('status') != 'completed':
            raise ProviderError('OPENAI_RESPONSE_INCOMPLETE', 'Provider did not complete the response; no tool calls executed')
        if data.get('model') != self.settings.model:
            raise ProviderError('OPENAI_MODEL_MISMATCH', 'Response model differs from configured fixed snapshot')
        output = data.get('output')
        if not isinstance(output, list): raise ProviderError('OPENAI_RESPONSE_INVALID', 'Missing response output')
        texts, calls, call_ids = [], [], set()
        for item in output:
            if not isinstance(item, dict): raise ProviderError('OPENAI_RESPONSE_INVALID', 'Invalid output item')
            if item.get('type') == 'function_call':
                if item.get('name') not in self._tool_names:
                    raise ProviderError('TOOL_NOT_ALLOWED', 'Model requested a tool outside this role')
                cid = item.get('call_id')
                if not _safe_id(cid) or cid in call_ids: raise ProviderError('OPENAI_RESPONSE_INVALID', 'Invalid or duplicate tool call ID')
                call_ids.add(cid)
                try: arguments = json.loads(item['arguments'], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (KeyError, ValueError, TypeError):
                    raise ProviderError('OPENAI_ARGUMENTS_INVALID', 'Tool arguments are not finite JSON') from None
                if not isinstance(arguments, dict): raise ProviderError('OPENAI_ARGUMENTS_INVALID', 'Tool arguments must be an object')
                calls.append({'name': item['name'], 'arguments': arguments})
            elif item.get('type') == 'message':
                content = item.get('content')
                if not isinstance(content,list) or not all(isinstance(p,dict) for p in content):
                    raise ProviderError('OPENAI_RESPONSE_INVALID', 'Invalid message content')
                for part in content:
                    if part.get('type') == 'refusal': raise ProviderError('OPENAI_REFUSAL', 'Model declined the request')
                    if part.get('type') == 'output_text' and isinstance(part.get('text'), str): texts.append(part['text'])
            elif item.get('type') == 'reasoning':
                if not item.get('encrypted_content'):
                    raise ProviderError('OPENAI_REASONING_STATE_MISSING', 'Cannot safely replay reasoning without encrypted state')
            else: raise ProviderError('OPENAI_RESPONSE_INVALID', 'Unsupported output type')
        return {'text': '\n'.join(texts), 'tool_calls': calls}

    def __call__(self, *, model, messages):
        if model != self.settings.model: raise ProviderError('OPENAI_MODEL_MISMATCH', 'Runtime and provider models must match')
        if len(self.calls) >= 32: raise ProviderError('OPENAI_CALL_LIMIT', 'Use one bounded provider instance per run')
        payload = {'model': model, 'input': self._input(messages), 'tools': self.tools,
                   'tool_choice': 'auto', 'parallel_tool_calls': False, 'store': False,
                   'reasoning': {'effort': 'none'}, 'include': ['reasoning.encrypted_content'],
                   'max_output_tokens': self.settings.max_output_tokens}
        data = canonical(payload).encode()
        budget, reservation, timeout = begin_paid_call(model,
            minimum_input_tokens=INPUT_TOKEN_RESERVATION_FLOOR,
            output_tokens=self.settings.max_output_tokens, timeout_seconds=self.settings.timeout_seconds)
        event = {'attempt': len(self.calls) + 1, 'request_hash': hashlib.sha256(data).hexdigest(),
                 'status': 'started', 'model': model, 'timeout_seconds': timeout}
        self.calls.append(event); started = time.monotonic()
        accounted_usage = None
        req = request.Request(ENDPOINT, data=data, method='POST', headers={
            'Authorization': 'Bearer ' + self.settings.api_key, 'Content-Type': 'application/json'})
        try:
            with self._open(req, timeout=timeout) as response:
                event.update(http_status=response.status, request_id=_safe_id(response.headers.get('x-request-id')))
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000: raise ProviderError('OPENAI_RESPONSE_TOO_LARGE', 'Provider response exceeded 2 MB')
            try: body = json.loads(raw)
            except (ValueError, UnicodeError): raise ProviderError('OPENAI_RESPONSE_INVALID', 'Provider response was not JSON') from None
            if not isinstance(body, dict):
                raise ProviderError('OPENAI_RESPONSE_INVALID', 'Provider response must be an object')
            if not isinstance(body.get('usage',{}),dict):
                raise ProviderError('OPENAI_RESPONSE_INVALID', 'Invalid response usage')
            # Even an incomplete/rejected model output can have billed usage.
            # Keep only numeric accounting fields, never arbitrary provider data.
            usage = {k: v for k, v in body.get('usage', {}).items()
                     if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int and v >= 0}
            for field, detail, total in (('input_tokens_details', 'cached_tokens', 'input_tokens'),
                                          ('output_tokens_details', 'reasoning_tokens', 'output_tokens')):
                details = body.get('usage', {}).get(field)
                value = details.get(detail) if isinstance(details, dict) else None
                if type(value) is int and 0 <= value <= usage.get(total, -1):
                    usage[field] = {detail: value}
            event.update(response_id=_safe_id(body.get('id')), usage=usage)
            if body.get('model') == self.settings.model:
                accounted_usage = usage
            normalized = self._normalize(body)
            event.update(status='completed')
            assistant = {'role': 'assistant', 'content': normalized['text'], 'tool_calls': normalized['tool_calls']}
            self._outputs[digest(messages + [assistant])] = body['output']
            return normalized
        except error.HTTPError as exc:
            event.update(status='failed', http_status=exc.code, request_id=_safe_id(exc.headers.get('x-request-id')))
            # Body is deliberately never logged or included in exceptions.
            if exc.code == 429 or 500 <= exc.code <= 599:
                event['error_code'] = 'OPENAI_TRANSIENT_HTTP'
                raise ConnectionError('OPENAI_TRANSIENT_HTTP') from None
            code = {401: 'OPENAI_AUTH_FAILED', 403: 'OPENAI_ACCESS_DENIED', 404: 'OPENAI_MODEL_UNAVAILABLE'}.get(exc.code, 'OPENAI_REQUEST_REJECTED')
            event['error_code'] = code
            raise ProviderError(code, 'OpenAI rejected the request; inspect configuration and account access locally') from None
        except (TimeoutError, socket.timeout):
            event.update(status='failed', error_code='OPENAI_TIMEOUT')
            raise TimeoutError('OPENAI_TIMEOUT') from None
        except error.URLError:
            event.update(status='failed', error_code='OPENAI_CONNECTION_FAILED')
            raise ConnectionError('OPENAI_CONNECTION_FAILED') from None
        except ProviderError as exc:
            event.update(status='failed', error_code=exc.code)
            raise
        finally:
            usage = accounted_usage or {}
            event['cost'] = reservation.settle(usage.get('input_tokens'), usage.get('output_tokens'))
            event['elapsed_seconds'] = round(time.monotonic() - started, 3)
            budget.check()
