"""Order Fulfillment request options over the unchanged common OpenAI adapter.

History replay, response normalization, settings, and error types stay shared.
The common adapter fixes request options inside __call__, so this subclass owns
the B request/usage boundary instead of changing a request after it was hashed.
"""
from __future__ import annotations

import hashlib
import json
import socket
import time
from urllib import error, request

from ..model import canonical, digest
from ..openai_provider import (
    ENDPOINT, MODEL, OpenAIProvider as CommonOpenAIProvider, OpenAISettings,
    ProviderError, _safe_id, INPUT_TOKEN_RESERVATION_FLOOR,
)
from ..harness import begin_paid_call
from .tool_specs import function_tools


class OpenAIProvider(CommonOpenAIProvider):
    """One B run, with opt-in tool batching and role-defined final JSON."""

    def __init__(self, settings, tools, *, opener=None, parallel_tool_calls=False,
                 require_initial_lookup=False):
        if type(parallel_tool_calls) is not bool:
            raise ProviderError('OPENAI_CONFIG_INVALID', 'parallel_tool_calls must be a boolean')
        specs = function_tools(tools)
        if type(require_initial_lookup) is not bool or (require_initial_lookup and set(tools) != {'lookup'}):
            raise ProviderError('OPENAI_CONFIG_INVALID', 'Initial lookup mode requires a boolean and only lookup')
        # Let the common constructor initialize its transport and replay state.
        # B schemas are selected locally; no common registry is changed.
        super().__init__(settings, ('lookup',), opener=opener)
        self.tools = specs
        self._tool_names = set(tools)
        self.parallel_tool_calls = parallel_tool_calls
        self.require_initial_lookup = require_initial_lookup

    def public_config(self):
        return dict(self.settings.public_config(), parallel_tool_calls=self.parallel_tool_calls,
                    require_initial_lookup=self.require_initial_lookup)

    def _tool_choice(self):
        return 'auto'

    def _text_format(self):
        return None

    def __call__(self, *, model, messages):
        if model != self.settings.model:
            raise ProviderError('OPENAI_MODEL_MISMATCH', 'Runtime and provider models must match')
        if len(self.calls) >= 32:
            raise ProviderError('OPENAI_CALL_LIMIT', 'Use one bounded provider instance per run')
        tool_choice = self._tool_choice()
        # Common runtime owns these paired tool results and still validates the
        # selected evidence. Require one real read, not a fixed lookup sequence.
        if self.require_initial_lookup and not any(
                message.get('role') == 'tool' and message.get('name') == 'lookup' for message in messages):
            tool_choice = 'required'
        payload = {'model': model, 'input': self._input(messages), 'tools': self.tools,
                   'tool_choice': tool_choice, 'parallel_tool_calls': self.parallel_tool_calls, 'store': False,
                   'reasoning': {'effort': 'none'}, 'include': ['reasoning.encrypted_content'],
                   'max_output_tokens': self.settings.max_output_tokens}
        text_format = self._text_format()
        if text_format is not None:
            payload['text'] = {'format': text_format}
        data = canonical(payload).encode()
        budget, reservation, timeout = begin_paid_call(model,
            minimum_input_tokens=INPUT_TOKEN_RESERVATION_FLOOR,
            output_tokens=self.settings.max_output_tokens, timeout_seconds=self.settings.timeout_seconds)
        event = {'attempt': len(self.calls) + 1, 'request_hash': hashlib.sha256(data).hexdigest(),
                 'status': 'started', 'model': model, 'timeout_seconds': timeout}
        self.calls.append(event)
        started = time.monotonic()
        accounted_usage = None
        req = request.Request(ENDPOINT, data=data, method='POST', headers={
            'Authorization': 'Bearer ' + self.settings.api_key, 'Content-Type': 'application/json'})
        try:
            with self._open(req, timeout=timeout) as response:
                event.update(http_status=response.status, request_id=_safe_id(response.headers.get('x-request-id')))
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ProviderError('OPENAI_RESPONSE_TOO_LARGE', 'Provider response exceeded 2 MB')
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ProviderError('OPENAI_RESPONSE_INVALID', 'Provider response was not JSON') from None
            if not isinstance(body, dict) or not isinstance(body.get('usage', {}), dict):
                raise ProviderError('OPENAI_RESPONSE_INVALID', 'Invalid response usage')
            # A rejected/incomplete response may still have billed usage. Keep
            # safe counts before shared normalization, without releasing tools.
            event.update(response_id=_safe_id(body.get('id')),
                usage={k: v for k, v in body.get('usage', {}).items()
                       if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int and v >= 0})
            # Keep the common adapter's bounded usage detail fields as well.
            for field, detail, total in (('input_tokens_details', 'cached_tokens', 'input_tokens'),
                                          ('output_tokens_details', 'reasoning_tokens', 'output_tokens')):
                details = body.get('usage', {}).get(field)
                value = details.get(detail) if isinstance(details, dict) else None
                if type(value) is int and 0 <= value <= event['usage'].get(total, -1):
                    event['usage'][field] = {detail: value}
            if body.get('model') == self.settings.model:
                accounted_usage = event['usage']
            normalized = self._normalize(body)
            event['status'] = 'completed'
            assistant = {'role': 'assistant', 'content': normalized['text'], 'tool_calls': normalized['tool_calls']}
            self._outputs[digest(messages + [assistant])] = body['output']
            return normalized
        except error.HTTPError as exc:
            event.update(status='failed', http_status=exc.code, request_id=_safe_id(exc.headers.get('x-request-id')))
            if exc.code == 429 or 500 <= exc.code <= 599:
                event['error_code'] = 'OPENAI_TRANSIENT_HTTP'
                raise ConnectionError('OPENAI_TRANSIENT_HTTP') from None
            code = {401: 'OPENAI_AUTH_FAILED', 403: 'OPENAI_ACCESS_DENIED',
                    404: 'OPENAI_MODEL_UNAVAILABLE'}.get(exc.code, 'OPENAI_REQUEST_REJECTED')
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
