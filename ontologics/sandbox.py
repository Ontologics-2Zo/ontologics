"""Restricted parser controller/client. Docker authority stays out of the web host.

The controller accepts bytes, a digest and a fixed job kind over a private Unix
socket. It cannot fetch URLs, run caller commands or mount caller paths. Docker
is a defense in depth boundary, not a claim of VM-grade hostile-code isolation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

from .service import DomainError

VERSION = 'sandbox-1'
MAX_INPUT = 5_000_000
MAX_OUTPUT = 1_000_000
MAX_WIRE = 7_000_000
JOBS = frozenset(('olimex_ods', 'history_json'))
_PROBES = frozenset(('isolation_probe', 'timeout_probe', 'output_probe', 'memory_probe', 'pids_probe'))


class SandboxError(DomainError):
    pass


def fail(code, message, details=None):
    raise SandboxError(code, message, details)


@dataclass(frozen=True)
class JobSpec:
    kind: str
    sha256: str
    artifact_id: str
    schema_version: str = VERSION

    def __post_init__(self):
        if (not isinstance(self.kind, str) or self.kind not in JOBS or self.schema_version != VERSION or
                not isinstance(self.sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.sha256) or
                self.artifact_id != 'sha256:' + self.sha256):
            fail('SANDBOX_JOB_INVALID', 'Use a supported content-addressed parser job')


def _content(spec, data):
    limit = 200_000 if spec.kind == 'history_json' else MAX_INPUT
    if not isinstance(data, bytes) or not 1 <= len(data) <= limit:
        fail('SANDBOX_INPUT_LIMIT', 'Parser input exceeds its fixed byte limit')
    if hashlib.sha256(data).hexdigest() != spec.sha256:
        fail('SANDBOX_HASH_MISMATCH', 'Parser input does not match its digest')


def validate_result(kind, checksum, raw):
    """Treat output as untrusted even when a job exited successfully."""
    try:
        if len(raw) > MAX_OUTPUT:
            raise ValueError()
        data = json.loads(raw.decode('utf-8'))
        if (not isinstance(data, dict) or set(data) != {'schema_version', 'sha256', 'kind', 'result'} or
                data['schema_version'] != VERSION or data['sha256'] != checksum or data['kind'] != kind):
            raise ValueError()
        result = data['result']
        if kind == 'olimex_ods':
            if not isinstance(result, list) or not 1 <= len(result) <= 4096:
                raise ValueError()
            positions = set()
            for row in result:
                if (not isinstance(row, dict) or set(row) != {'sheet', 'row', 'locator', 'quantity', 'raw_value', 'package', 'positions', 'population'} or
                        type(row['row']) is not int or row['row'] < 1 or type(row['quantity']) is not int or row['quantity'] < 1 or
                        row['population'] not in ('populated', 'unknown') or not isinstance(row['positions'], list) or
                        len(row['positions']) != row['quantity']):
                    raise ValueError()
                for name in ('sheet', 'locator', 'raw_value', 'package'):
                    if not isinstance(row[name], str) or not 1 <= len(row[name]) <= 4096:
                        raise ValueError()
                if row['locator'] != "'" + row['sheet'] + "'!A" + str(row['row']) + ':D' + str(row['row']):
                    raise ValueError()
                for position in row['positions']:
                    if not isinstance(position, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}', position) or position in positions:
                        raise ValueError()
                    positions.add(position)
        elif kind == 'history_json':
            if not isinstance(result, dict):
                raise ValueError()
            # Domain fields, source lineage and time rules remain in receipts/model.
            json.dumps(result, allow_nan=False)
        elif kind in _PROBES:
            if not isinstance(result, dict):
                raise ValueError()
        else:
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        fail('SANDBOX_OUTPUT_INVALID', 'Parser result failed size, format or digest validation')


class DockerRunner:
    """Trusted controller only. Never pass this object or daemon socket to Agents."""
    def __init__(self, image_id, *, docker='/usr/local/bin/docker', timeout_seconds=15):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
            raise ValueError('Pin a locally built immutable Docker image ID')
        if not Path(docker).is_absolute() or not 0.1 <= timeout_seconds <= 30:
            raise ValueError('Use an absolute Docker binary and bounded deadline')
        self.image_id, self.docker, self.timeout = image_id, docker, timeout_seconds
        self._slots = threading.BoundedSemaphore(2)

    def parse(self, kind, content, sha256):
        spec = JobSpec(kind, sha256, 'sha256:' + sha256)
        _content(spec, content)
        return self._run(spec.kind, content, spec.sha256)

    def probe(self, kind, *, cancel_event=None):
        """Trusted test-only entry; the socket controller never accepts probes."""
        if kind not in _PROBES:
            fail('SANDBOX_JOB_INVALID', 'Unknown fixed test probe')
        content = b'public synthetic sandbox probe'
        return self._run(kind, content, hashlib.sha256(content).hexdigest(), cancel_event=cancel_event)

    def _run(self, kind, content, checksum, *, cancel_event=None):
        if not self._slots.acquire(blocking=False):
            fail('SANDBOX_BUSY', 'Parser capacity reached; retry later')
        started, name = time.monotonic(), 'ontologics-parser-' + uuid.uuid4().hex
        proc, container_created = None, False
        try:
            with tempfile.TemporaryDirectory(prefix='ontologics-parser-') as folder:
                path = Path(folder) / 'input'
                path.write_bytes(content)
                path.chmod(0o444)
                # Mount exactly one controlled regular file, never the repo/home/socket.
                argv = [self.docker, 'create', '--pull=never', '--name', name,
                    '--network=none', '--read-only', '--user=65532:65532', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges=true', '--memory=128m', '--memory-swap=128m',
                    '--cpus=0.5', '--pids-limit=32', '--ulimit', 'nofile=64:64',
                    '--ulimit', 'fsize=1048576:1048576', '--log-driver=none',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777',
                    '--mount', 'type=bind,src=' + str(path) + ',dst=/input/data,readonly',
                    self.image_id, kind, checksum]
                try:
                    created = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             timeout=5, check=False)
                    if created.returncode:
                        fail('SANDBOX_UNAVAILABLE', 'Fixed parser image or Docker is unavailable')
                    container_created = True
                    observed = subprocess.run([self.docker, 'inspect', '--format', '{{.HostConfig.NetworkMode}}', name],
                                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
                    if observed.returncode or observed.stdout.strip() != b'none':
                        fail('SANDBOX_JOB_FAILED', 'Container network isolation configuration did not match policy')
                    proc = subprocess.Popen([self.docker, 'start', '--attach', name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except (OSError, subprocess.TimeoutExpired):
                    fail('SANDBOX_UNAVAILABLE', 'Docker parser controller is unavailable')
                out, err = bytearray(), bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ, out)
                    selector.register(proc.stderr, selectors.EVENT_READ, err)
                    while selector.get_map():
                        if cancel_event is not None and cancel_event.is_set():
                            fail('SANDBOX_CANCELLED', 'Parser cancelled; container and descendants are removed')
                        remaining = self.timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            fail('SANDBOX_TIMEOUT', 'Parser deadline exceeded; job is removed')
                        for key, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            key.data.extend(chunk)
                            if len(out) + len(err) > MAX_OUTPUT:
                                fail('SANDBOX_OUTPUT_LIMIT', 'Parser output exceeded its byte limit')
                    proc.wait(timeout=max(0.1, self.timeout - (time.monotonic() - started)))
                if proc.returncode:
                    # Do not relay raw Docker errors or stderr (paths/secrets/host data).
                    code = 'SANDBOX_UNAVAILABLE' if proc.returncode == 125 else 'SANDBOX_JOB_FAILED'
                    fail(code, 'Restricted parser failed; no host fallback was used', {'exit_code': proc.returncode})
                result = validate_result(kind, checksum, bytes(out))
                result['sandbox'] = {'image_id': self.image_id, 'duration_ms': round((time.monotonic() - started) * 1000, 3),
                    'network': 'none', 'observed_network_mode': observed.stdout.decode().strip(),
                    'schema_version': VERSION, 'output_sha256': hashlib.sha256(out).hexdigest()}
                return result
        finally:
            try:
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    proc.stdout.close()
                    proc.stderr.close()
                # Also handles a create command interrupted before start.
                try:
                    cleanup = subprocess.run([self.docker, 'rm', '-f', name], stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, timeout=5, check=False)
                    if container_created and cleanup.returncode:
                        fail('SANDBOX_CLEANUP_FAILED', 'Controller could not confirm job cleanup')
                except (OSError, subprocess.TimeoutExpired):
                    # Cleanup uncertainty is surfaced rather than claimed successful.
                    if container_created:
                        fail('SANDBOX_CLEANUP_FAILED', 'Controller could not confirm job cleanup')
            finally:
                self._slots.release()


class SandboxClient:
    """Limited local IPC; no Docker capability or host paths in a job request."""
    def __init__(self, socket_path):
        self.socket_path = str(Path(socket_path).absolute())

    def parse(self, kind, content, sha256):
        spec = JobSpec(kind, sha256, 'sha256:' + sha256)
        _content(spec, content)
        request = {'kind': kind, 'sha256': sha256, 'artifact_id': spec.artifact_id,
                   'schema_version': VERSION, 'content_base64': base64.b64encode(content).decode()}
        encoded = json.dumps(request, separators=(',', ':')).encode() + b'\n'
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(35)
                conn.connect(self.socket_path)
                conn.sendall(encoded)
                with conn.makefile('rb') as stream:
                    raw = stream.readline(MAX_OUTPUT + 8192)
            if not raw.endswith(b'\n') or len(raw) > MAX_OUTPUT + 4096:
                fail('SANDBOX_OUTPUT_INVALID', 'Controller response exceeds its bound')
            answer = json.loads(raw)
            if (not isinstance(answer, dict) or type(answer.get('ok')) is not bool or
                    set(answer) != ({'ok', 'data'} if answer['ok'] else {'ok', 'error'})):
                fail('SANDBOX_OUTPUT_INVALID', 'Controller response is not a supported envelope')
            if not answer.get('ok'):
                error = answer.get('error', {})
                if not isinstance(error, dict):
                    fail('SANDBOX_OUTPUT_INVALID', 'Controller error is not a supported object')
                code = error.get('code', '')
                if code not in {'SANDBOX_BUSY', 'SANDBOX_JOB_FAILED', 'SANDBOX_TIMEOUT', 'SANDBOX_UNAVAILABLE',
                                'SANDBOX_OUTPUT_INVALID', 'SANDBOX_OUTPUT_LIMIT', 'SANDBOX_CLEANUP_FAILED'}:
                    code = 'SANDBOX_JOB_FAILED'
                fail(code, 'Restricted parsing failed; no host fallback was used')
            data = answer['data']
            if not isinstance(data, dict):
                fail('SANDBOX_OUTPUT_INVALID', 'Controller output is not an object')
            result = validate_result(kind, sha256, json.dumps({k: v for k, v in data.items() if k != 'sandbox'}, allow_nan=False).encode())
            meta = data.get('sandbox')
            if not isinstance(meta, dict) or meta.get('network') != 'none' or not re.fullmatch(r'sha256:[0-9a-f]{64}', meta.get('image_id', '')):
                fail('SANDBOX_OUTPUT_INVALID', 'Missing controller isolation metadata')
            result['sandbox'] = meta
            return result
        except SandboxError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            fail('SANDBOX_UNAVAILABLE', 'Restricted parser controller is unavailable')


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            raw = self.rfile.readline(MAX_WIRE + 1)
            if len(raw) > MAX_WIRE or not raw.endswith(b'\n'):
                fail('SANDBOX_JOB_INVALID', 'Request size or framing invalid')
            req = json.loads(raw)
            if not isinstance(req, dict) or set(req) != {'kind', 'sha256', 'artifact_id', 'schema_version', 'content_base64'}:
                fail('SANDBOX_JOB_INVALID', 'Only fixed parser job fields are allowed')
            spec = JobSpec(req['kind'], req['sha256'], req['artifact_id'], req['schema_version'])
            content = base64.b64decode(req['content_base64'], validate=True)
            data = self.server.runner.parse(spec.kind, content, spec.sha256)
            answer = {'ok': True, 'data': data}
        except SandboxError as exc:
            answer = {'ok': False, 'error': {'code': exc.code}}
        except Exception:
            answer = {'ok': False, 'error': {'code': 'SANDBOX_JOB_INVALID'}}
        self.wfile.write(json.dumps(answer, ensure_ascii=False, allow_nan=False).encode() + b'\n')


class Controller(socketserver.UnixStreamServer):
    def __init__(self, path, runner):
        path = Path(path).absolute()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.stat().st_mode & 0o077 or path.parent.stat().st_uid != os.getuid():
            raise ValueError('Controller socket directory must be private (0700)')
        if path.exists() or path.is_symlink():
            raise ValueError('Use an unused socket path; do not replace another controller')
        self.runner = runner
        old_mask = os.umask(0o077)
        try:
            super().__init__(str(path), _Handler)
        finally:
            os.umask(old_mask)
        path.chmod(0o600)

    def server_close(self):
        super().server_close()
        Path(self.server_address).unlink(missing_ok=True)


def main():
    import signal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--image-id', required=True, help='sha256 image ID from local reviewed build')
    parser.add_argument('--docker', default='/usr/local/bin/docker')
    args = parser.parse_args()
    def stop(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    with Controller(args.socket, DockerRunner(args.image_id, docker=args.docker)) as server:
        print(json.dumps({'ready': True, 'schema_version': VERSION, 'image_id': args.image_id}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
