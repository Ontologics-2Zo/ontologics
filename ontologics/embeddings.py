"""One fixed embedding model; safe bounded transport, no database or Agent rights."""
from __future__ import annotations

import json
import math
import socket
import time
from urllib import error, request

from .model import canonical
from .openai_provider import OpenAISettings, ProviderError, _NoRedirect, _safe_id


EMBEDDING_MODEL = 'text-embedding-3-small'
EMBEDDING_DIMENSIONS = 1536
ENDPOINT = 'https://api.openai.com/v1/embeddings'
MAX_TEXT_BYTES = 8192
MAX_BATCH_SIZE = 32
MAX_RESPONSE_BYTES = 2_000_000


class OpenAIEmbeddings:
    """Use the existing key/timeout configuration; never changes the chat model.

    At most two transport attempts per embed() call. Only timeout, HTTP 429 and
    HTTP 5xx are retried. Inputs are not silently truncated; malformed or partial
    vectors never leave the adapter. No API body/header/input text is logged.
    """
    model = EMBEDDING_MODEL
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, settings, opener=None):
        if not isinstance(settings, OpenAISettings):
            raise ValueError('Use existing OpenAISettings with a local API key')
        self.settings = settings
        self._open = opener or request.build_opener(_NoRedirect()).open
        self.calls = []

    def public_config(self):
        return {'provider': 'openai', 'endpoint': ENDPOINT, 'model': self.model,
                'dimensions': self.dimensions, 'timeout_seconds': self.settings.timeout_seconds,
                'max_attempts': 2, 'max_batch_size': MAX_BATCH_SIZE, 'max_text_bytes': MAX_TEXT_BYTES}

    @staticmethod
    def validate_texts(texts):
        if not isinstance(texts, list) or not 1 <= len(texts) <= MAX_BATCH_SIZE:
            raise ProviderError('EMBEDDING_INPUT_INVALID', 'Provide 1..32 nonempty text documents')
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ProviderError('EMBEDDING_INPUT_INVALID', 'Embedding input must be nonempty text')
            try:
                size = len(text.encode('utf-8'))
            except UnicodeError:
                raise ProviderError('EMBEDDING_INPUT_INVALID', 'Embedding input must be valid UTF-8') from None
            if size > MAX_TEXT_BYTES:
                raise ProviderError('EMBEDDING_INPUT_TOO_LARGE', 'A document exceeds 8192 UTF-8 bytes; no text was truncated')

    def _normalize(self, body, count):
        if not isinstance(body, dict):
            raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Embedding response must be an object')
        if body.get('model') != self.model:
            raise ProviderError('OPENAI_EMBEDDING_MODEL_MISMATCH', 'Response differs from the fixed embedding model')
        data = body.get('data')
        if not isinstance(data, list) or len(data) != count:
            raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Embedding response does not cover the complete input batch')
        vectors = [None] * count
        for item in data:
            if (not isinstance(item, dict) or type(item.get('index')) is not int or
                    not 0 <= item['index'] < count or vectors[item['index']] is not None):
                raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Invalid or duplicate embedding input index')
            vector = item.get('embedding')
            if (not isinstance(vector, list) or len(vector) != self.dimensions or
                    any(type(v) not in (float, int) or abs(v) > 1e30 or not math.isfinite(v) for v in vector) or
                    not any(vector)):
                raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Use finite nonzero vectors of the fixed dimension')
            vectors[item['index']] = vector
        usage = body.get('usage', {})
        if not isinstance(usage, dict) or any(type(usage[k]) is not int or usage[k] < 0
                                            for k in ('prompt_tokens', 'total_tokens') if k in usage):
            raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Invalid embedding usage metadata')
        return vectors, {k: usage[k] for k in ('prompt_tokens', 'total_tokens') if k in usage}

    def embed(self, texts):
        self.validate_texts(texts)
        payload = canonical({'input': texts, 'model': self.model, 'encoding_format': 'float',
                             'dimensions': self.dimensions}).encode()
        for attempt in range(1, 3):
            event = {'attempt': attempt, 'status': 'started', 'model': self.model,
                     'dimensions': self.dimensions, 'input_count': len(texts)}
            self.calls.append(event)
            started = time.monotonic()
            req = request.Request(ENDPOINT, data=payload, method='POST', headers={
                'Authorization': 'Bearer ' + self.settings.api_key, 'Content-Type': 'application/json'})
            try:
                with self._open(req, timeout=self.settings.timeout_seconds) as response:
                    event.update(http_status=response.status, request_id=_safe_id(response.headers.get('x-request-id')))
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                        raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Unexpected or oversized embedding response')
                try:
                    body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError):
                    raise ProviderError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Embedding response was not finite JSON') from None
                vectors, usage = self._normalize(body, len(texts))
                event.update(status='completed', usage=usage)
                return vectors
            except error.HTTPError as exc:
                transient = exc.code == 429 or 500 <= exc.code <= 599
                code = ('OPENAI_EMBEDDING_TRANSIENT_HTTP' if transient else
                        {401: 'OPENAI_AUTH_FAILED', 403: 'OPENAI_ACCESS_DENIED', 404: 'OPENAI_MODEL_UNAVAILABLE'}
                        .get(exc.code, 'OPENAI_REQUEST_REJECTED'))
                event.update(status='failed', http_status=exc.code,
                             request_id=_safe_id((exc.headers or {}).get('x-request-id')), error_code=code)
                # Never read the error body, which may echo request text or credentials.
                exc.close()
                if transient and attempt == 1:
                    continue
                raise ProviderError(code, 'Embedding request failed; inspect safe status and local account configuration') from None
            except (TimeoutError, socket.timeout):
                event.update(status='failed', error_code='OPENAI_TIMEOUT')
                if attempt == 1:
                    continue
                raise ProviderError('OPENAI_TIMEOUT', 'Embedding timeout retry budget exhausted') from None
            except error.URLError as exc:
                timeout = isinstance(exc.reason, (TimeoutError, socket.timeout))
                code = 'OPENAI_TIMEOUT' if timeout else 'OPENAI_CONNECTION_FAILED'
                event.update(status='failed', error_code=code)
                if timeout and attempt == 1:
                    continue
                raise ProviderError(code, 'Embedding connection failed') from None
            except ProviderError as exc:
                event.update(status='failed', error_code=exc.code)
                raise
            except Exception:
                event.update(status='failed', error_code='OPENAI_CONNECTION_FAILED')
                raise ProviderError('OPENAI_CONNECTION_FAILED', 'Embedding transport failed') from None
            finally:
                event['elapsed_seconds'] = round(time.monotonic() - started, 3)
