"""Owned PostgreSQL processes and fail-closed boundaries for the B example runner.

This development runner deliberately requires macOS sandbox-exec and PostgreSQL
with pgvector. It never discovers or reuses a database from environment files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import threading
import uuid
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]


def checked_path(path: Path) -> Path:
    """Reject aliases before opening a path, including links in its parents."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute canonical path required')
    for part in [*reversed(path.parents), path]:
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError('symlink path rejected')
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(checked_path(path).read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    with checked_path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def source_files() -> list[Path]:
    # Four known source directories only; no artifact, database or credential scan.
    paths = []
    for relative in ('ontologics', 'ontologics/order_fulfillment',
                     'examples', 'examples/order_fulfillment'):
        paths.extend(sorted((ROOT / relative).glob('*.py')))
    return [checked_path(path) for path in paths]


def source_digest() -> str:
    values = {str(path.relative_to(ROOT)): sha(path) for path in source_files()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def clean_environment(output: Path) -> dict[str, str]:
    return dict(PATH='/usr/local/bin:/usr/bin:/bin', LANG='C', LC_ALL='C',
                TZ='Asia/Seoul', HOME=str(output / 'home'),
                TMPDIR=str(output / 'temp'), PYTHONDONTWRITEBYTECODE='1',
                PGPASSFILE=str(output / 'no_passfile'),
                PGSERVICEFILE=str(output / 'no_service'),
                PGSYSCONFDIR=str(output / 'no_sysconf'),
                PGSSLMODE='disable', PGGSSENCMODE='disable')


def postgres_bin(explicit: str | None) -> Path:
    candidate = Path(explicit) if explicit else Path('/usr/local/opt/postgresql@17/bin')
    candidate = candidate.resolve(strict=True)
    if not candidate.is_absolute() or not all(
        (candidate / name).is_file() and os.access(candidate / name, os.X_OK)
        for name in ('initdb', 'pg_ctl', 'createdb')
    ):
        raise ValueError('PostgreSQL bin directory is incomplete')
    return candidate


def prepare(output: Path, *, mode: str, cases: list[str], pg_bin: Path,
            authorization=None, gate=None, gate_hash=None) -> dict:
    output = checked_path(output)
    if any(part.startswith('pgdata') or part in {'.pgdata', '.git', '.venv', '.aws', '.ssh'}
           or part.startswith(('task09_', 'task10_')) for part in output.parts):
        raise ValueError('output cannot be inside historical/protected resources')
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / 'artifacts/local/order_fulfillment'):
        raise ValueError('checkout outputs belong under artifacts/local/order_fulfillment')
    if mode not in ('free', 'live') or not cases:
        raise ValueError('explicit free/live mode and nonempty cases required')
    if output.exists():
        raise ValueError('output must be new; never resume or overwrite a run')
    if not output.parent.is_dir():
        raise ValueError('output parent must already exist')
    # Inspect all immutable inputs before creating resources.
    inputs = {name: sha(ROOT / name) for name in SAFE_INPUTS}
    if inputs != SAFE_INPUTS:
        raise ValueError('reviewed safe input hash changed')
    sources = source_digest()
    if mode == 'live':
        from .b_run_transport import validate_authorization
        validate_authorization(authorization, run_id=str(output), cases=cases,
                               free_gate_sha256=gate_hash, source_sha256=sources)
        if (not gate or gate.get('passed') is not True or
                gate.get('mode') != 'free' or gate.get('cases') != cases or
                gate.get('source_sha256') != sources or
                gate.get('safe_inputs') != inputs or
                gate.get('actual_api_attempts') != 0 or
                gate.get('server_stopped') is not True):
            raise ValueError('matching completed free gate required')
    output.mkdir(mode=0o700)
    for name in ('home', 'temp', 'inputs', 'submissions'):
        (output / name).mkdir(mode=0o700)
    copied = []
    for name, expected in inputs.items():
        target = output / 'inputs' / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(checked_path(ROOT / name).read_bytes())
        if sha(target) != expected:
            raise ValueError('safe input copy mismatch')
        copied.append(dict(path=name, sha256=expected))
    token = uuid.uuid4().hex[:16]
    sock = Path('/private/tmp') / ('ontol-b-' + token)
    if sock.exists():
        raise ValueError('socket scope already exists')
    settings = dict(run_id=str(output), mode=mode, cases=cases, output=str(output),
                    input_root=str(output / 'inputs'), pg_bin=str(pg_bin),
                    socket=str(sock), pgdata=str(output / 'pgdata'),
                    port='56911', user='b_' + token, dbname='b_' + token,
                    source_sha256=sources, safe_inputs=inputs,
                    code_files=[str(p) for p in source_files()],
                    site_packages=[str(p) for p in (ROOT / '.venv/lib').glob('python*/site-packages')],
                    authorization=authorization, free_gate_sha256=gate_hash,
                    endpoint_ips=[])
    settings['url'] = ('postgresql://' + settings['user'] + '@/' + settings['dbname'] +
                       '?host=' + quote(str(sock), safe='') + '&port=56911&sslmode=disable&gssencmode=disable')
    if mode == 'live':
        settings['endpoint_ips'] = sorted({row[4][0] for row in socket.getaddrinfo(
            'api.openai.com', 443, socket.AF_INET, socket.SOCK_STREAM)})
        if not settings['endpoint_ips']:
            raise ValueError('no pinned endpoint addresses')
    save(output / 'settings.json', settings)
    save(output / 'safe_inputs.json', copied)
    save(output / 'inputs/safe_input_manifest.json', inputs)
    (output / 'protected.sb').write_text(sandbox_policy(settings), encoding='utf-8')
    return settings


def sandbox_policy(settings: dict) -> str:
    literal = lambda p: '(literal ' + json.dumps(str(p)) + ')'
    subpath = lambda p: '(subpath ' + json.dumps(str(p)) + ')'
    output = Path(settings['output'])
    files = settings['code_files'] + [str(ROOT / name) for name in SAFE_INPUTS]
    # Restrict user and temporary data even when launching outside Codex.
    lines = ['(version 1)', '(allow default)', '(deny network*)',
             '(deny file-write*)',
             '(allow file-write* ' + subpath(output) + ' ' + subpath(settings['socket']) + ' (literal "/dev/null"))',
             '(deny file-read-data (subpath "/Users") (subpath "/private/tmp") (subpath "/usr/local/var"))',
             '(allow file-read-data ' + ' '.join(literal(p) for p in files) + ')',
             '(allow file-read-data ' + ' '.join(literal(p) for p in
                 [ROOT, ROOT / 'examples', ROOT / 'examples/order_fulfillment',
                  ROOT / 'ontologics', ROOT / 'ontologics/order_fulfillment']) + ')',
             '(allow file-read-data ' + subpath(output) + ' ' + subpath(settings['socket']) +
             ' ' + ' '.join(subpath(p) for p in settings['site_packages']) + ')',
             '(allow network* (local unix-socket ' + subpath(settings['socket']) +
             ') (remote unix-socket ' + subpath(settings['socket']) + '))',
             '(deny file-read-data (regex #"/[.]env($|[.])") (regex #"/([.]pgpass|[.]pg_service[.]conf|compose[.]ya?ml|credentials[.]json)$"))']
    if settings['mode'] == 'live':
        lines.append('(allow file-read-data ' + literal(ROOT / '.env.local') + ')')
        # macOS Seatbelt accepts only * or localhost here. The Python audit hook
        # below pins exact approved IPs and opens them only during an HTTP send.
        lines.append('(allow network-outbound (remote tcp "*:443"))')
    return '\n'.join(lines) + '\n'


def launch(settings: dict) -> int:
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise RuntimeError('macOS sandbox-exec is required; no unprotected fallback')
    output = Path(settings['output'])
    # -I/-B/-S exclude inherited paths, site hooks and bytecode writes.
    code = ('import sys;sys.path[:0]=' + repr([str(ROOT), *settings['site_packages']]) +
            ';from examples.order_fulfillment.b_run_isolation import own_run;'
            'sys.exit(own_run(' + repr(str(output / 'settings.json')) + '))')
    command = ['/usr/bin/sandbox-exec', '-f', str(output / 'protected.sb'),
               sys.executable, '-I', '-B', '-S', '-c', code]
    child = subprocess.Popen(command, cwd=ROOT, env=clean_environment(output))
    previous = {}
    def forward(signum, frame):
        if child.poll() is None:
            child.send_signal(signum)
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, forward)
    try:
        return child.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def own_run(settings_path: str) -> int:
    settings = json.loads(checked_path(Path(settings_path)).read_text())
    output = Path(settings['output']); pg = Path(settings['pg_bin'])
    socket_path = Path(settings['socket']); data = Path(settings['pgdata'])
    env = clean_environment(output); records = []; pid = None; attempted = False
    failed = False; stopped = False; summary = None; cleanup_errors = []
    def terminate(signum, frame):
        raise RuntimeError('owner interrupted')
    old_term = signal.signal(signal.SIGTERM, terminate)

    def execute(name, args, *, required=True):
        with (output / (name + '.stdout.txt')).open('xb') as stdout, \
                (output / (name + '.stderr.txt')).open('xb') as stderr:
            child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
            try:
                code = child.wait(timeout=None if name == 'campaign' else 60)
            except BaseException:
                # A terminated owner cannot leave a model/review worker running.
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait()
                raise
        records.append(dict(name=name, returncode=code))
        if required and code:
            raise RuntimeError(name + ' failed')
        return code

    try:
        if data.exists() or socket_path.exists():
            raise ValueError('resources must be fresh')
        socket_path.mkdir(mode=0o700)
        execute('initdb', [str(pg / 'initdb'), '-D', str(data), '-U', settings['user'],
                          '--auth-local=trust', '--auth-host=reject', '--no-locale', '--encoding=UTF8'])
        options = ("-c listen_addresses='' -c unix_socket_directories=" + str(socket_path) +
                   ' -c unix_socket_permissions=0700 -c port=' + settings['port'] +
                   ' -c dynamic_shared_memory_type=mmap -c ssl=off -c logging_collector=off'
                   ' -c log_statement=none -c log_temp_files=-1')
        attempted = True
        execute('pg_start', [str(pg / 'pg_ctl'), '-D', str(data), '-l', str(output / 'postgres.log'),
                             '-o', options, '-w', 'start'])
        pid = int((data / 'postmaster.pid').read_text().splitlines()[0])
        execute('createdb', [str(pg / 'createdb'), '-h', str(socket_path), '-p', settings['port'],
                             '-U', settings['user'], '--maintenance-db=postgres', settings['dbname']])
        code = ('import sys;sys.path[:0]=' + repr([str(ROOT), *settings['site_packages']]) +
                ';from examples.order_fulfillment.b_run_isolation import guarded_campaign;'
                'sys.exit(guarded_campaign(' + repr(settings_path) + '))')
        execute('campaign', [sys.executable, '-I', '-B', '-S', '-c', code])
        summary = json.loads((output / 'campaign' / 'summary.json').read_text())
    except BaseException as error:
        failed = True
        save(output / 'execution_failure.json', dict(error_type=type(error).__name__,
                                                   stopped=True, automatic_paid_rerun=False))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        def cleanup(name, args):
            try:
                return execute(name, args, required=False)
            except BaseException as error:
                cleanup_errors.append(dict(operation=name, error_type=type(error).__name__))
                return None
        if attempted:
            status = cleanup('status_before_stop', [str(pg / 'pg_ctl'), '-D', str(data), 'status'])
            if status != 3:
                rc = cleanup('pg_stop', [str(pg / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'])
                if rc != 0:
                    cleanup('pg_stop_fallback', [str(pg / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'])
            status = cleanup('status_after_stop', [str(pg / 'pg_ctl'), '-D', str(data), 'status'])
        else:
            status = 3
        alive = False
        if pid:
            try:
                os.kill(pid, 0); alive = True
            except ProcessLookupError:
                pass
        if socket_path.exists() and not list(socket_path.iterdir()):
            socket_path.rmdir()
        stopped = status == 3 and not alive and not (data / 'postmaster.pid').exists() and not socket_path.exists()
        save(output / 'shutdown.json', dict(server_stopped=stopped, status_exit=status,
             owned_pid=pid, owned_pid_absent=not alive, postmaster_pid_absent=not (data / 'postmaster.pid').exists(),
             socket_absent=not socket_path.exists(), pgdata_retained=True, cleanup_errors=cleanup_errors))
        save(output / 'processes.json', records)
        signal.signal(signal.SIGTERM, old_term); signal.signal(signal.SIGINT, old_int)
    if not failed and not cleanup_errors and stopped and summary and summary.get('passed') is True:
        save(output / 'free_gate.json' if settings['mode'] == 'free' else output / 'completion.json',
             dict(passed=True, mode=settings['mode'], cases=settings['cases'],
                  source_sha256=settings['source_sha256'], safe_inputs=settings['safe_inputs'],
                  actual_api_attempts=summary['actual_api_attempts'], server_stopped=True,
                  summary_sha256=sha(output / 'campaign' / 'summary.json'),
                  shutdown_sha256=sha(output / 'shutdown.json')))
        return 0
    return 1


def guarded_campaign(settings_path: str) -> int:
    settings = json.loads(checked_path(Path(settings_path)).read_text())
    output = Path(settings['output']); events = []; connections = []; connection_rejections = []
    sys.pycache_prefix = str(output / 'bytecode')
    key_window = False; send_window = False
    def terminate(signum, frame):
        raise RuntimeError('campaign interrupted')
    old_term = signal.signal(signal.SIGTERM, terminate)
    allowed = frozenset(settings['code_files'] + [str(ROOT / name) for name in SAFE_INPUTS])
    pgdata = Path(settings['pgdata'])
    key_path = ROOT / '.env.local'

    def audit(event, args):
        reason = None
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.path.abspath(os.fsdecode(args[0])))
            writing = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if path.is_relative_to(ROOT) or path.is_relative_to(output):
                checked_path(path)
            if writing and not path.is_relative_to(output):
                reason = 'write outside new run'
            if path.is_relative_to(pgdata):
                reason = 'database file access'
            if any(part.startswith('.env') or part in {'.pgdata', '.aws', '.ssh', '.postgresql'} for part in path.parts) or path.name in {'.pgpass', '.pg_service.conf', 'compose.yaml', 'compose.yml', 'credentials.json'}:
                if not (key_window and path == key_path and not writing):
                    reason = 'credential/environment input blocked'
            if not writing and path.is_relative_to(ROOT) and not path.is_relative_to(output):
                if str(path) not in allowed and not any(path.is_relative_to(Path(p)) for p in settings['site_packages']) and not (key_window and path == key_path):
                    reason = 'outside explicit safe source files'
        if event == 'socket.connect':
            address = args[1]
            if address == settings['socket'] + '/.s.PGSQL.' + settings['port']:
                pass
            elif not (send_window and isinstance(address, tuple) and address[0] in settings['endpoint_ips'] and address[1] == 443):
                reason = 'connection outside owned socket/authorized endpoint'
        if event in {'socket.bind', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
            reason = 'network discovery/server not required'
        if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.forkpty'}:
            reason = 'subprocess forbidden in campaign'
        if reason:
            events.append(dict(event=event, reason=reason,
                               path=str(path) if event == 'open' else None))
            raise PermissionError(reason)

    sys.addaudithook(audit)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    connect_code = psycopg.Connection.connect.__func__.__code__
    busy = threading.local()

    def check_address(values):
        expected = dict(host=settings['socket'], port=settings['port'],
                        dbname=settings['dbname'], user=settings['user'], sslmode='disable', gssencmode='disable')
        # Shared Database.connect supplies its fixed 10-second connect timeout.
        optional_timeout = values.get('connect_timeout')
        if optional_timeout is not None and str(optional_timeout) != '10':
            raise PermissionError('unexpected database connection timeout')
        if {key: value for key, value in values.items() if key != 'connect_timeout'} != expected:
            connection_rejections.append(dict(expected=expected,
                actual={key: str(values.get(key)) for key in expected},
                unexpected_keys=sorted(set(values) - set(expected) - {'connect_timeout'})))
            raise PermissionError('database address is not the new owned scope')

    def observe(frame, event, result):
        if frame.f_code is connect_code and not getattr(busy, 'active', False):
            if event == 'call':
                check_address(conninfo_to_dict(frame.f_locals['conninfo'], **frame.f_locals['kwargs']))
            elif event == 'return' and isinstance(result, psycopg.Connection):
                busy.active = True
                try:
                    row = result.execute("SELECT current_database() AS db,current_user AS actor,current_setting('data_directory') AS pgdata,current_setting('listen_addresses') AS listen,inet_server_addr() AS addr,current_setting('unix_socket_directories') AS sockets").fetchone()
                    values = list(row.values()) if isinstance(row, dict) else list(row)
                    if values != [settings['dbname'], settings['user'], settings['pgdata'], '', None, settings['socket']]:
                        connection_rejections.append(dict(actual_owned_server=values))
                        raise PermissionError('actual connection differs from owned DB')
                    connections.append(dict(db=values[0], actor=values[1], pgdata=values[2],
                                            listen=values[3], tcp_address=values[4], socket=values[5],
                                            thread=threading.current_thread().name))
                finally:
                    busy.active = False

    sys.setprofile(observe); threading.setprofile(observe)
    original_resolver = socket.getaddrinfo

    def pinned_resolver(host, port, *args, **kwargs):
        if send_window and host == 'api.openai.com' and port == 443:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 443))
                    for ip in settings['endpoint_ips']]
        raise PermissionError('DNS outside authorized send')

    socket.getaddrinfo = pinned_resolver
    try:
        if source_digest() != settings['source_sha256'] or {
            name: sha(Path(settings['input_root']) / name) for name in SAFE_INPUTS
        } != settings['safe_inputs'] or {name: sha(ROOT / name) for name in SAFE_INPUTS} != settings['safe_inputs'] or json.loads(
            (Path(settings['input_root']) / 'safe_input_manifest.json').read_text()
        ) != settings['safe_inputs']:
            raise ValueError('sealed code/input changed before campaign')
        from .b_run_workflow import run_campaign
        backend = None; provider_settings = None
        if settings['mode'] == 'live':
            from .b_run_transport import load_authorized_settings, AuthorizedBackend
            kwargs = dict(run_id=settings['run_id'], cases=settings['cases'],
                          free_gate_sha256=settings['free_gate_sha256'], source_sha256=settings['source_sha256'])
            key_window = True
            try:
                provider_settings = load_authorized_settings(ROOT, settings['authorization'], **kwargs)
            finally:
                key_window = False
            actual = AuthorizedBackend(provider_settings, settings['authorization'], **kwargs)
            from contextlib import contextmanager

            @contextmanager
            def backend(req, *, timeout):
                nonlocal send_window
                if (output / 'submissions/stop.json').exists():
                    raise RuntimeError('operator stopped run')
                send_window = True
                try:
                    with actual(req, timeout=timeout) as response:
                        yield response
                finally:
                    send_window = False

        def wait_review(request):
            # Operator/independent reviewer writes the requested exact-bound JSON.
            # Timeouts stop the run and the owning process shuts its DB down.
            import time
            if (output / 'submissions/stop.json').exists():
                raise RuntimeError('operator stopped run')
            kind = request['kind']; case = request['case']
            path = output / 'submissions' / (case + '-' + kind + '.json')
            save(output / 'submissions' / (case + '-' + kind + '.requested.json'), request)
            deadline = time.monotonic() + 3600
            while not path.exists():
                if (output / 'submissions' / 'stop.json').exists():
                    raise RuntimeError('operator stopped run')
                if time.monotonic() >= deadline:
                    raise TimeoutError('review wait expired')
                time.sleep(1)
            if (output / 'submissions/stop.json').exists():
                raise RuntimeError('operator stopped run')
            checked_path(path)
            if path.stat().st_size > 64_000:
                raise ValueError('review JSON too large')
            return json.loads(path.read_text())

        summary = run_campaign(database_url=settings['url'], input_root=Path(settings['input_root']),
                               output=output / 'campaign', cases=settings['cases'],
                               offline=settings['mode'] == 'free', settings=provider_settings,
                               backend=backend, review_wait=wait_review if settings['mode'] == 'live' else None)
        return 0 if summary.get('passed') is True and not events else 1
    except BaseException as error:
        save(output / 'campaign_failure.json', dict(error_type=type(error).__name__, stopped=True,
                                                   automatic_paid_rerun=False))
        return 1
    finally:
        sys.setprofile(None); threading.setprofile(None); socket.getaddrinfo = original_resolver
        signal.signal(signal.SIGTERM, old_term)
        save(output / 'safety.json', dict(access_denials=events, connections=connections,
                                        connection_rejections=connection_rejections,
                                        credentials_read=settings['mode'] == 'live',
                                        external_api_allowed=settings['mode'] == 'live',
                                        original_psycopg_connect_unchanged=True))


# Fixed reviewed synthetic/source inputs. Updated only with an explicit fixture review.
SAFE_INPUTS = {
    "data/catalog.json": "c71e28685b4a6a2cfec1e9ed3616a341d9a03ad28120577bb5c618888e67ec98",
    "data/demo_records.json": "bf87153316dab7ed6e073d6edc7f4d378a5732c393f95c9c56e90751fdd1ca3c",
    "data/initial_graph.jsonld": "6e1fa833e64e67aebe7f5c8560848acc77f485b216e00f86f712fd2ad1ba55a5",
    "data/order_fulfillment/b_contract_expected.json": "eff8576170062bca019371d00fb9c3c52872e0815a48cf7887422472f5d4bed1",
    "data/order_fulfillment/b_contract_fixture.json": "cb0a743b6997909b7f1edf56650177c5a9213758a85879d85a933d92ae6aa8d7",
    "data/order_fulfillment/b_contract_graph.jsonld": "711e5312939bb9e6c006c2b6fb9726e7cf7cdc2c85c6e7de1b873fe07d03a69e",
    "data/order_fulfillment/b_delivery_expected.json": "0051c5734f2697b4ba5b01fd0c870ce54afbb6cddff2d68a043ca7c7588ae440",
    "data/order_fulfillment/b_delivery_fixture.json": "723c6b2b290420329c4e97a1dcf327b95d1e8ecc41e09d160a438ea673ee2dec",
    "data/order_fulfillment/b_delivery_graph.jsonld": "f5a3fc3987730d4fcb664649748db9c9ae2ebabc9f6ab808cb7d3a7890e6b849",
    "data/order_fulfillment/b_delivery_mixed_expected.json": "473b15b84511e56ba606289f057cc6852ab5efbfe0d66964fea3f4ded879b5c7",
    "data/order_fulfillment/b_delivery_mixed_fixture.json": "8f91334740bc10a280de132624875d2986fb1a4252e1f12bb0eca48f137a114a",
    "data/order_fulfillment/b_delivery_mixed_graph.jsonld": "7d33334f166a65166328d233a7f6b337a76ae448ca31b2860c13a0b8634e543f",
    "data/order_fulfillment/b_fulfillment_expected.json": "cb48c4befa95a1359298620a9026daa544c48ad79dc2b5ab3fe7c3ff28a299ee",
    "data/order_fulfillment/b_fulfillment_fixture.json": "ff9a822eb9865c589659934a9484dccc882b11dcf8a6d2295a841a06cf3cef8f",
    "data/order_fulfillment/b_fulfillment_graph.jsonld": "70eacee7fbb616c5f242e7f26d5888d779db8afcaf334127930a22f37b2e7c71",
    "data/order_fulfillment/b_production_expected.json": "2df0d1eaec52495bc957178775160a44ac8ae5a3617ebd397e59c038ab539802",
    "data/order_fulfillment/b_production_fixture.json": "3aaa2e9664ecc262631d5263e63d42de51f604e4307c8af7b1837c2b86444302",
    "data/order_fulfillment/b_production_graph.jsonld": "0a2df0a1219e5ef2df36e706aedee78caba9529a328a74367b7370b4f398d9cf",
    "data/order_fulfillment/b_recalculation_expected.json": "e23c6d5834451676d3dd71957677721e0b566d21ca45962f6c22a73caec9fc1f",
    "data/order_fulfillment/b_recalculation_fixture.json": "e6388976736549569d84a5c9803f5051211a503eae4b17305078181774482158",
    "data/order_fulfillment/b_response_expected.json": "135a46c4ff625e17748fbb0bdd9124faf4597e30afd9445aba5fc1d0a1584b0a",
    "data/order_fulfillment/sources/b_demo_contract_01.md": "bbe0b4a2ad05bcb14e0eb477f90d9ae5f25c67a706a906e606b8e8023eda67ae",
    "data/order_fulfillment/sources/b_demo_delivery_01.md": "510c474c1a68ab1cc34d3b323d3a1760f8ccda977fd127df0700c2b66a3e2d74",
    "data/order_fulfillment/sources/b_demo_delivery_mixed_01.md": "392789dd5993d2c6b242d91f85aa8e06978c694f4c76bdfb3a847c7a5cb09544",
    "data/order_fulfillment/sources/b_demo_fulfillment_01.md": "2a74f77ce99d599227e97d8e85b681d54cfddc7e7076044110908954132f0622",
    "data/order_fulfillment/sources/b_demo_production_01.md": "6b18f76917db81837a4a06891d9a8331b412c19d1d62f46d0e3a681bc442c810",
    "data/order_fulfillment/sources/b_demo_recalculation_01.md": "4f7cd72e3710525e41a161d04ef44d5faaf485953a5c1f2a09358f249bf726cc",
    "data/sources/ESP32-POE-product-page.html": "b36d1810d5866abe4a7724bedec84c30a5863dafe6725e2a2f3b30a7380f6ce3",
    "data/sources/ESP32-POE-user-manual.odt": "153628e491fab04822b429af33256cdf2bc7b3bf0668daff60c1281ef8d64d6f",
    "data/sources/ESP32-PoE_Rev_L-BOM-WROOM.ods": "ef4f04d46ca8f3961f6ac528320011679a14c57007717b5972c32551e9c51a5e",
    "data/sources/ESP32-PoE_Rev_L-BOM-WROOM.pdf": "e268ebf562c5e6b36dab7bcd377922c7cb5883ad88ba024f8b949d0e9cae89d0",
    "data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods": "773c8b3fb0fa9f9c354586d12fd6bffd02b156b94d07e23e671c1205ab918e95",
    "data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.pdf": "30e2e7c881d9a7c74cd95b1a63af4ac2ac7ae623ea05ec16e9faaf5ad19f3e9a",
    "data/sources/OLIMEX-LICENSE.txt": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "data/sources/OLIMEX-README.md": "420a6bcdddc0b7ef5fcb0f3bd6b60e15e184175c49d92e1c2dbc76ed3bdfee6c",
    "data/sources/manifest.json": "b676e91e3e6cd51ac3ebecedb3c40971879baf228a8af7890aec99a91471401e",
    "ontology/tbox.ttl": "f478550b606de4cedde1251cf2e1a0ff4406b1230723a7e7a0e39a59465f8e92"
}
