"""Reviewed parser entrypoint and fixed isolation probes; no dynamic code."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import resource
import socket
import sys
import struct
import time
import zipfile

sys.path.insert(0, '/job')  # Fixed reviewed code only; isolated Python ignores cwd/PYTHONPATH.
from ontologics.ingest import parse_olimex_ods

path = Path('/input/data')
kind, expected = sys.argv[1:]
content = path.read_bytes()
if len(content) > 5_000_000 or hashlib.sha256(content).hexdigest() != expected:
    raise ValueError('INPUT_HASH_OR_SIZE')

if kind == 'olimex_ods':
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 256 or sum(x.file_size for x in infos) > 20_000_000:
            raise ValueError('ARCHIVE_LIMIT')
        for item in infos:
            pieces = Path(item.filename).parts
            if item.filename.startswith('/') or '..' in pieces or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ARCHIVE_PATH_OR_LINK')
    result = parse_olimex_ods(path)
elif kind == 'history_json':
    if len(content) > 200_000:
        raise ValueError('JSON_LIMIT')
    result = json.loads(content.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
    if not isinstance(result, dict):
        raise ValueError('JSON_OBJECT_REQUIRED')
elif kind == 'isolation_probe':
    def denied(action):
        try:
            action()
            return False
        except (OSError, ValueError):
            return True
    network = denied(lambda: socket.create_connection(('192.0.2.1', 9), timeout=.2))
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    interfaces = sorted(name for _, name in socket.if_nameindex())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as interface_socket:
        interface_flags = {name: struct.unpack('H', fcntl.ioctl(interface_socket.fileno(), 0x8913,
                           struct.pack('256s', name.encode()))[16:18])[0] for name in interfaces}
    routes = [line.split() for line in Path('/proc/net/route').read_text().splitlines()[1:]]
    result = {'uid': os.getuid(), 'network_denied': network,
        'interfaces': interfaces, 'interface_flags': interface_flags,
        'no_ipv4_default_route': not any(row[1] == '00000000' for row in routes),
        'root_write_denied': denied(lambda: Path('/forbidden').write_text('synthetic')),
        'input_write_denied': denied(lambda: path.write_bytes(b'synthetic')),
        'host_home_absent': not Path('/Users').exists(), 'docker_socket_absent': not Path('/var/run/docker.sock').exists(),
        'secret_env_absent': not any(k in os.environ for k in ('OPENAI_API_KEY', 'DATABASE_URL', 'AWS_SECRET_ACCESS_KEY')),
        'no_new_privileges': status.get('NoNewPrivs', '').strip() == '1',
        'capabilities_empty': int(status.get('CapEff', '1').strip(), 16) == 0,
        'seccomp_filter': status.get('Seccomp', '').strip() == '2',
        'memory_limit_bytes': int(Path('/sys/fs/cgroup/memory.max').read_text()),
        'pids_limit': int(Path('/sys/fs/cgroup/pids.max').read_text()),
        'cpu_quota': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
        'nofile_limit': resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
elif kind == 'timeout_probe':
    # Only fixed test code. Child must be removed with its container on timeout.
    pid = os.fork()
    time.sleep(60)
    result = {'unexpected_completion': True, 'child': bool(pid)}
elif kind == 'output_probe':
    sys.stdout.write('x' * 1_100_000)
    sys.stdout.flush()
    sys.exit(0)
elif kind == 'memory_probe':
    excessive = bytearray(256 * 1024 * 1024)
    result = {'unexpected_allocation': len(excessive)}
elif kind == 'pids_probe':
    children = []
    denied = False
    try:
        for _ in range(40):
            try:
                pid = os.fork()
            except OSError:
                denied = True
                break
            if pid == 0:
                time.sleep(5)
                os._exit(0)
            children.append(pid)
    finally:
        for pid in children:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
    result = {'pids_denied': denied, 'created_children': len(children)}
else:
    raise ValueError('UNSUPPORTED_FIXED_JOB')
print(json.dumps({'schema_version': 'sandbox-1', 'kind': kind, 'sha256': expected, 'result': result}, ensure_ascii=False, allow_nan=False))
