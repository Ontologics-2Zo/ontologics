"""Task10 disposable PG launcher. Never reads model settings or installs packages.

Run with the verified Python 3.12.10. --serve stays open until Ctrl+C; --test
runs the requested unittest modules (default: full discover). Logs omit DB URI.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

CACHE=[Path.home()/'.cache/uv/archive-v0'/n for n in ('P-JgI8-NqlnL1x5X','aB5dYWhrZQ_azIiW','uGeoOBzfPLI_JvIX','8DnPxdtq1JvVLLjM')]
IMAGE='pgvector/pgvector:0.8.7-pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
REPO=Path(__file__).resolve().parents[1]


def prepare_test_checkout(source, destination):
    """Copy current bytes for tests with fixed ROOT paths; no shared receipt writes."""
    return Path(shutil.copytree(source,destination,ignore=shutil.ignore_patterns(
        ".env*", "__pycache__", ".superpowers")))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--serve',action='store_true');g.add_argument('--test',action='store_true')
    p.add_argument('--port',type=int,default=0);p.add_argument('modules',nargs='*');args=p.parse_args()
    if sys.version.split()[0]!='3.12.10':p.error('Use verified Python 3.12.10')
    sys.path[:0]=[str(x) for x in CACHE]+[str(REPO)]
    for package,version in [('psycopg','3.3.6'),('psycopg-binary','3.3.6'),('typing_extensions','4.16.0'),('pypdf','6.12.2')]:
        if importlib.metadata.version(package)!=version:p.error('Required existing cache version missing: '+package)
    import psycopg
    if not 0<=args.port<=65535:p.error('Port must be 0 or a local port')
    out=Path(tempfile.mkdtemp(prefix='task10-run-'))
    name='ontologics-task10-'+uuid.uuid4().hex[:12]
    password=secrets.token_hex(24)
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('OPENAI_','AZURE_OPENAI_')):env.pop(key)
    env.update(POSTGRES_PASSWORD=password,POSTGRES_USER='task10',POSTGRES_DB='task10',PYTHONDONTWRITEBYTECODE='1')
    report=dict(container=name,image=IMAGE,model_api_calls=0,mode='serve' if args.serve else 'test')
    cid=None;url=None;child=None;code=1
    test_tree=None;test_parent=None
    def docker(*cmd):
        result=subprocess.run(['docker',*cmd],env=env,text=True,capture_output=True)
        if result.returncode:raise RuntimeError('Docker operation failed: '+cmd[0]+' (check Docker Desktop access)')
        return result.stdout.strip()
    def redacted(text):return text.replace(password,'[TEST_SECRET]').replace(url or 'invalid://unused','[TEST_DATABASE]')
    try:
        docker('image','inspect',IMAGE)
        cid=docker('run','--detach','--rm','--pull=never','--name',name,'--label','ontologics.task=task10',
            '--publish','127.0.0.1::5432','--mount','type=tmpfs,destination=/var/lib/postgresql/data',
            '--env','POSTGRES_PASSWORD','--env','POSTGRES_USER','--env','POSTGRES_DB',IMAGE)
        ports=json.loads(docker('inspect',cid,'--format','{{json .NetworkSettings.Ports}}'))['5432/tcp'][0]
        if ports['HostIp']!='127.0.0.1':raise RuntimeError('Loopback binding required')
        url='postgresql://task10:'+password+'@127.0.0.1:'+ports['HostPort']+'/task10'
        for _ in range(100):
            try:
                with psycopg.connect(url,autocommit=True) as db:
                    db.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
                    report['backend']=db.execute("SELECT current_setting('server_version'),extversion FROM pg_extension WHERE extname='vector'").fetchone()
                break
            except psycopg.OperationalError:time.sleep(.2)
        else:raise RuntimeError('New test PostgreSQL did not become ready')
        env.update(TEST_DATABASE_URL=url,DATABASE_URL=url,PYTHONPATH=os.pathsep.join(str(x) for x in CACHE))
        print('Task10 fresh sandbox; log: '+str(out),flush=True)
        if args.serve:
            # A child isolates process environment; its finally closes all three schemas.
            command=[sys.executable,'-c','from examples.materials_ui import serve; serve('+str(args.port)+')']
            child=subprocess.Popen(command,cwd=REPO,env=env,start_new_session=True)
            try:code=child.wait()
            except KeyboardInterrupt:
                child.send_signal(signal.SIGINT);code=child.wait(timeout=30)
        else:
            command=[sys.executable,'-m','unittest',*(args.modules or ['discover']),'-v']
            test_parent=Path(tempfile.mkdtemp(prefix='.task10-tests-',dir=REPO.parent))
            test_tree=prepare_test_checkout(REPO,test_parent/'repo')
            result=subprocess.run(command,cwd=test_tree,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            (out/'tests.log').write_text(redacted(result.stdout))
            print(redacted(result.stdout[-5500:]),flush=True);code=result.returncode
        with psycopg.connect(url,autocommit=True) as db:
            report['schemas_remaining']=[x[0] for x in db.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('public','information_schema') AND schema_name NOT LIKE 'pg_%'")]
        if report['schemas_remaining']:code=1
    except Exception as exc:
        print(type(exc).__name__+': '+redacted(str(exc)),flush=True)
        report['error']=type(exc).__name__;code=1
    finally:
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        if cid:
            docker('rm','--force','--volumes',cid)
            absent=subprocess.run(['docker','inspect',cid],env=env,capture_output=True).returncode!=0
            report['container_removed']=absent
        if test_tree is not None:
            shutil.rmtree(test_parent)
        report['test_checkout_removed']=test_tree is None or not test_tree.exists()
        report['shared_receipts_touched']=False
        report['exit_code']=code
        (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('Task10 cleanup: '+json.dumps(report,ensure_ascii=False),flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
