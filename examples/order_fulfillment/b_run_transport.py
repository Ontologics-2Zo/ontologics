"""Bounded B example transport; importing this module never loads credentials."""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import threading
import uuid
from urllib import error, request

MODEL = 'gpt-5.4-mini-2026-03-17'
ENDPOINT = 'https://api.openai.com/v1/responses'
CASES = ['cq01_intake', 'cq01_promise', 'cq02_normal', 'cq03_normal',
         'cq04_normal', 'cq04_mixed', 'cq05_normal', 'cq05_mixed',
         'cq06_defects11', 'cq06_transport2h']
MAX_RESPONSE_BYTES = 2_000_000


class Blocked(RuntimeError):
    """The run cannot advance across this boundary."""


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _write(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _json_write(path, value):
    _write(path, (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode())


def _preserve_response(path, raw, bearer_key):
    """Preserve bytes, except an exact current credential echo must be redacted."""
    secret = bearer_key.encode() if bearer_key else b''
    echoed = bool(secret and secret in raw)
    saved = raw.replace(secret, b'[REDACTED_CURRENT_BEARER]') if echoed else raw
    _write(path, saved)
    if echoed:
        _json_write(str(path) + '.preservation.json', {
            'credential_echo_redacted': True,
            'original_sha256': hashlib.sha256(raw).hexdigest(),
            'preserved_sha256': hashlib.sha256(saved).hexdigest(),
            'raw_bytes_verbatim': False,
        })
    return saved, echoed


class Ledger:
    """Append-only, fsynced reservations; an interrupted case cannot be resumed."""

    def __init__(self, path, *, cases=CASES, total_cap=None, offline=False):
        self.path = Path(path)
        self.cases = list(cases)
        if type(offline) is not bool:
            raise Blocked('offline ledger flag must be boolean')
        self.offline = offline
        if (not self.cases or len(set(self.cases)) != len(self.cases)
                or any(case not in CASES for case in self.cases)
                or self.cases != [case for case in CASES if case in self.cases]):
            raise Blocked('invalid representative sequence')
        self.total_cap = 4 * len(self.cases) if total_cap is None else total_cap
        if type(self.total_cap) is not int or not 1 <= self.total_cap <= 4 * len(self.cases):
            raise Blocked('invalid request budget')
        self._owner = uuid.uuid4().hex

    def _edit(self, action):
        with self.path.open('a+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            try:
                rows = [json.loads(line) for line in stream if line.strip()]
            except (ValueError, UnicodeError) as exc:
                raise Blocked('invalid ledger; restart denied') from exc
            if any(not isinstance(row, dict) or 'event' not in row for row in rows):
                raise Blocked('invalid ledger')
            event = action(rows)
            if event is not None:
                event = dict(event, at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                stream.seek(0, 2)
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                rows.append(event)
            return rows

    def rows(self):
        return self._edit(lambda rows: None)

    def _active(self, rows, case):
        if any(row['event'] == 'stopped' for row in rows):
            raise Blocked('campaign stopped')
        started = [row for row in rows if row['event'] == 'started']
        if (not started or started[-1].get('case') != case
                or started[-1].get('owner') != self._owner
                or any(row['event'] == 'accepted' and row.get('case') == case for row in rows)):
            raise Blocked('case inactive or restart denied')

    def begin(self, case):
        def action(rows):
            if case not in self.cases or any(row['event'] == 'stopped' for row in rows):
                raise Blocked('campaign stopped or unknown case')
            if any(row.get('case') == case for row in rows):
                raise Blocked('case already started: restart denied')
            accepted = [row.get('case') for row in rows if row['event'] == 'accepted']
            index = self.cases.index(case)
            if accepted != self.cases[:index]:
                raise Blocked('prior automatic and semantic acceptance required')
            if len([row for row in rows if row['event'] == 'started']) != index:
                raise Blocked('another case remains active')
            return dict(event='started', case=case, owner=self._owner)
        self._edit(action)

    def reserve(self, case, payload_hash):
        if not _sha(payload_hash):
            raise Blocked('invalid payload digest')
        number = []
        def action(rows):
            self._active(rows, case)
            reserved = [row for row in rows if row['event'] == 'reserved']
            own = [row for row in reserved if row.get('case') == case]
            completed = [row for row in rows if row['event'] == 'http_result' and row.get('case') == case]
            if len(own) != len(completed):
                raise Blocked('unfinished request: replay denied')
            cq01_count = sum(row['case'].startswith('cq01_') for row in reserved)
            if len(own) >= 4 or len(reserved) >= self.total_cap or (case.startswith('cq01_') and cq01_count >= 8):
                raise Blocked('pre-send request budget exhausted')
            number.append(len(own) + 1)
            return dict(event='reserved', case=case, request=number[0], global_request=len(reserved) + 1,
                        payload_sha256=payload_hash, durable_before_http=True)
        self._edit(action)
        return number[0]

    def finish(self, case, number, body):
        def action(rows):
            self._active(rows, case)
            own = [row for row in rows if row['event'] == 'reserved' and row.get('case') == case]
            if not own or own[-1]['request'] != number or any(
                    row['event'] == 'http_result' and row.get('case') == case and row.get('request') == number for row in rows):
                raise Blocked('request result does not match reservation')
            usage = body.get('usage') if isinstance(body, dict) else None
            return dict(event='http_result', case=case, request=number,
                        status=body.get('status') if isinstance(body, dict) else None,
                        usage=usage, usage_missing=usage is None)
        self._edit(action)

    def stop(self, case, reason):
        def action(rows):
            if any(row['event'] == 'stopped' for row in rows):
                return None
            return dict(event='stopped', case=case, reason=reason)
        self._edit(action)

    def accept(self, case, auto_pass, review):
        def action(rows):
            self._active(rows, case)
            reserved = [row for row in rows if row['event'] == 'reserved' and row.get('case') == case]
            completed = [row for row in rows if row['event'] == 'http_result' and row.get('case') == case]
            offline_review = (self.offline and isinstance(review, dict)
                              and review.get('kind') == 'offline_wiring_scaffold_only'
                              and review.get('offline_simulated_review') is True
                              and review.get('passed') is True and review.get('case') == case
                              and _sha(review.get('bound_output_hash')))
            if (auto_pass is not True or not (_valid_review(review, case) or offline_review)
                    or not reserved or len(reserved) != len(completed)
                    or any(row.get('status') != 'completed' for row in completed)):
                raise Blocked('automatic and semantic acceptance failed')
            return dict(event='accepted', case=case, automatic=True, semantic_review=review)
        try:
            self._edit(action)
        except Blocked:
            self.stop(case, 'evaluation/semantic review failed')
            raise


class Response:
    def __init__(self, raw, *, status=200, headers=None):
        self.raw, self.status, self.headers = raw, status, headers or {}
        self._position = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        end = len(self.raw) if n < 0 else self._position + n
        value = self.raw[self._position:end]
        self._position += len(value)
        return value


@contextmanager
def _hard_deadline(seconds=30):
    if threading.current_thread() is not threading.main_thread():
        raise Blocked('live transport requires main-thread hard deadline')
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise Blocked('another real-time deadline is active')
    def expired(signum, frame):
        raise TimeoutError('hard request deadline exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Transport:
    def __init__(self, ledger, case, out, backend, *, paid_authorized=False, offline=True):
        if type(offline) is not bool or (not offline and paid_authorized is not True):
            raise Blocked('paid execution unapproved')
        self.ledger, self.case, self.out = ledger, case, Path(out)
        self.backend, self.offline, self.sent = backend, offline, 0

    def __call__(self, req, *, timeout=30):
        try:
            if self.offline:
                return self._call(req, timeout=timeout)
            with _hard_deadline():
                return self._call(req, timeout=timeout)
        except BaseException as exc:
            self.ledger.stop(self.case, type(exc).__name__)
            raise

    def _call(self, req, *, timeout):
        prefix = None
        authorization = req.get_header('Authorization', '')
        bearer_key = authorization[7:] if authorization.startswith('Bearer ') else ''
        try:
            if req.full_url != ENDPOINT or req.get_method() != 'POST' or timeout != 30:
                raise Blocked('endpoint/method/timeout mismatch')
            payload = json.loads(req.data)
            if (not isinstance(payload, dict) or payload.get('model') != MODEL
                    or type(payload.get('max_output_tokens')) is not int or payload.get('max_output_tokens') != 1024
                    or payload.get('reasoning') != {'effort': 'none'} or payload.get('store') is not False):
                raise Blocked('wire settings mismatch')
            number = self.ledger.reserve(self.case, hashlib.sha256(req.data).hexdigest())
            prefix = self.out / (self.case + '_http' + str(number))
            _write(str(prefix) + '_request.json', req.data)
            _json_write(str(prefix) + '_wire.json', dict(endpoint=ENDPOINT, method='POST', timeout_seconds=30,
                        max_output_tokens=1024, automatic_http_retries=0, offline_http_double=self.offline))
            def receive():
                self.sent += 1
                with self.backend(req, timeout=30) as response:
                    return response.read(MAX_RESPONSE_BYTES + 1), response.status, dict(response.headers)
            raw, status, headers = receive()
            _, echoed = _preserve_response(str(prefix) + '_response.raw.json', raw, bearer_key)
            if echoed:
                raise Blocked('credential echo response blocked')
            if len(raw) > MAX_RESPONSE_BYTES:
                raise Blocked('response too large')
            body = json.loads(raw)
            self.ledger.finish(self.case, number, body)
            if status != 200 or not isinstance(body, dict) or body.get('status') != 'completed' or not body.get('output'):
                raise Blocked('HTTP/incomplete/empty response')
            return Response(raw, status=status, headers=headers)
        except BaseException as exc:
            try:
                self._preserve_failure(exc, prefix, number if prefix is not None else None, bearer_key)
            finally:
                self.ledger.stop(self.case, type(exc).__name__)
            raise

    def _preserve_failure(self, exc, prefix, number, bearer_key):
        if prefix is not None:
            metadata = dict(error_type=type(exc).__name__, usage=None, usage_missing=True)
            if isinstance(exc, error.HTTPError):
                raw = exc.read(MAX_RESPONSE_BYTES + 1)
                raw, echoed = _preserve_response(str(prefix) + '_http_error.raw.json', raw, bearer_key)
                metadata['http_status'] = exc.code
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                self.ledger.finish(self.case, number, body)
                metadata.update(usage=body.get('usage'), usage_missing=body.get('usage') is None)
                if echoed:
                    metadata['credential_echo_redacted'] = True
                    _json_write(str(prefix) + '_error.json', metadata)
                    raise Blocked('credential echo response blocked') from None
            _json_write(str(prefix) + '_error.json', metadata)

_AUTH_FIELDS = {'approved', 'source', 'run_id', 'cases', 'per_case_request_cap', 'total_request_cap',
                'cq01_request_cap', 'max_output_tokens', 'timeout_seconds', 'automatic_retries',
                'model', 'endpoint', 'input_and_money_caps_unfixed_acknowledged',
                'free_gate_sha256', 'source_sha256'}


def validate_authorization(auth, *, run_id, cases, free_gate_sha256, source_sha256):
    expected_cases = list(cases)
    if (not isinstance(auth, dict) or set(auth) != _AUTH_FIELDS
            or not isinstance(run_id, str) or not run_id or auth.get('run_id') != run_id
            or not expected_cases or len(set(expected_cases)) != len(expected_cases)
            or expected_cases != [case for case in CASES if case in expected_cases]
            or auth.get('approved') is not True
            or auth.get('source') != 'explicit_user_confirmation_with_mandatory_free_gate'
            or auth.get('cases') != expected_cases
            or auth.get('per_case_request_cap') != 4
            or auth.get('total_request_cap') != 4 * len(expected_cases)
            or auth.get('cq01_request_cap') != 4 * sum(case.startswith('cq01_') for case in expected_cases)
            or auth.get('max_output_tokens') != 1024 or auth.get('timeout_seconds') != 30
            or auth.get('automatic_retries') != 0 or auth.get('model') != MODEL
            or auth.get('endpoint') != ENDPOINT
            or auth.get('input_and_money_caps_unfixed_acknowledged') is not True
            or not _sha(free_gate_sha256) or auth.get('free_gate_sha256') != free_gate_sha256
            or not _sha(source_sha256) or auth.get('source_sha256') != source_sha256):
        raise Blocked('fresh run-bound paid authorization required')
    for field in ('per_case_request_cap', 'total_request_cap', 'cq01_request_cap',
                  'max_output_tokens', 'timeout_seconds', 'automatic_retries'):
        if type(auth[field]) is not int:
            raise Blocked('authorization numeric fields must be integers')
    return True


def load_authorized_settings(root, auth, *, run_id, cases, free_gate_sha256, source_sha256, loader=None):
    validate_authorization(auth, run_id=run_id, cases=cases, free_gate_sha256=free_gate_sha256,
                           source_sha256=source_sha256)
    if loader is None:
        from ontologics.database import environment_values
        loader = environment_values
    from ontologics.order_fulfillment.openai_provider import OpenAISettings
    values = loader(env_file=Path(root) / '.env.local', environ={})
    key = values.get('OPENAI_API_KEY', '')
    del values
    return OpenAISettings.from_environment(environ={'OPENAI_API_KEY': key, 'OPENAI_MODEL': MODEL,
                'OPENAI_TIMEOUT_SECONDS': '30', 'OPENAI_MAX_OUTPUT_TOKENS': '1024'})


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class AuthorizedBackend:
    def __init__(self, settings, auth, *, run_id, cases, free_gate_sha256, source_sha256, opener=None):
        validate_authorization(auth, run_id=run_id, cases=cases, free_gate_sha256=free_gate_sha256,
                               source_sha256=source_sha256)
        if (settings.model != MODEL or settings.timeout_seconds != 30 or settings.max_output_tokens != 1024
                or not settings.api_key):
            raise Blocked('authorized settings mismatch')
        self.settings = settings
        self.open = opener if opener is not None else request.build_opener(
            request.ProxyHandler({}), NoRedirect()).open

    def __call__(self, req, *, timeout=30):
        if (req.full_url != ENDPOINT or req.get_method() != 'POST' or timeout != 30
                or req.get_header('Authorization') != 'Bearer ' + self.settings.api_key):
            raise Blocked('unapproved endpoint/credentials/timeout')
        return self.open(req, timeout=30)


def _valid_review(review, case):
    return (isinstance(review, dict) and review.get('case') == case and review.get('passed') is True
            and review.get('kind') == 'separate_korean_semantic_review'
            and _sha(review.get('report_sha256')) and _sha(review.get('bound_output_hash'))
            and isinstance(review.get('notes'), str) and bool(re.search('[가-힣]', review['notes'])))


def accept_after_review(ledger, case, report_path, review_path):
    try:
        raw = Path(report_path).read_bytes()
        report = json.loads(raw)
        review = json.loads(Path(review_path).read_text(encoding='utf-8'))
        runtime = report.get('runtime', {})
        checks = report.get('checks', {})
        if (report.get('case') != case or report.get('passed') is not True
                or runtime.get('status') != 'completed' or not isinstance(runtime.get('text'), str)
                or not runtime['text'].strip() or not isinstance(checks, dict) or not checks
                or any(value is not True for value in checks.values()) or not _valid_review(review, case)
                or review['report_sha256'] != hashlib.sha256(raw).hexdigest()
                or review['bound_output_hash'] != runtime.get('output_hash')):
            raise Blocked('automatic or exact-output Korean semantic acceptance failed')
        ledger.accept(case, True, review)
    except BaseException:
        ledger.stop(case, 'automatic or exact-output semantic acceptance failed')
        raise
