"""Local B workflow lab over the existing isolated CLI, without new dependencies.

python -m examples.order_fulfillment.b_web --port 8010 --cost-policy /absolute/public-policy.json
Only the isolated CLI reads the existing model credential. Each run owns a fresh
PostgreSQL instance; this adapter never opens the user's database or approves work.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import threading
from urllib.parse import urlsplit
import uuid

from .b_notebook import CASE_DETAILS, Demo, _read
from .b_run_isolation import ROOT, clean_environment
from .b_run_transport import CASES
from .b_task_workflow import case_task

STATIC = Path(__file__).with_name('web')
NOTICE = ('가상 업무 자료로 B의 CQ01~06을 확인합니다. 각 사례는 독립 입력이며 '
          '앞 단계의 저장 결과를 다음 단계로 전달하는 운영 흐름은 아닙니다. '
          '등록·사람 승인·출고·고객 발송은 수행하지 않습니다.')


class Lab:
    def __init__(self, cost_policy=None):
        self.policy_path = Path(cost_policy) if cost_policy else None
        self.runs = {}
        self.lock = threading.Lock()
        self.csrf_token = secrets.token_urlsafe(32)

    def live_readiness(self):
        from .b_model_preflight import load_policy, policy_from_snapshot
        from ontologics.openai_provider import MODEL
        result = dict(ready=False, model=MODEL, representative_max_cost_usd='1.00',
                      total_max_cost_usd=None, issues=[])
        if self.policy_path is None:
            result['issues'].append('서버 실행 시 --cost-policy로 기존 공개 비용 설정을 지정하세요.')
        else:
            try:
                policy = policy_from_snapshot(load_policy(str(self.policy_path)))
                result['total_max_cost_usd'] = policy.max_cost_usd
            except (OSError, ValueError, TypeError, KeyError):
                result['issues'].append('공개 비용 설정을 확인하세요.')
        if not (ROOT / '.env.local').is_file():
            result['issues'].append('기존 .env.local 모델 연결 설정이 필요합니다.')
        result['ready'] = not result['issues']
        result['notice'] = '설정 준비 상태입니다. 실제 API 연결 성공은 실행 결과에서 확인합니다.'
        return result

    def catalog(self):
        demo = Demo(CASES)
        rows = []
        for case in CASES:
            role, task = case_task(case)
            preview = demo.input_preview(case)
            rows.append(dict(id=case, label=CASE_DETAILS[case][0],
                             question=task.data().get('question') or CASE_DETAILS[case][1],
                             source_text=preview['source_text'], source_path=preview['source_path'],
                             source_sha256=preview['source_sha256'], input=task.data(),
                             role=role[0].SPEC.name))
        return dict(cases=rows, mode='scripted', notice=NOTICE, live=self.live_readiness(),
                    csrf_token=self.csrf_token)

    def start(self, body):
        if not isinstance(body, dict) or set(body) - {'cases', 'mode'}:
            raise ValueError('지원하지 않는 실행 설정입니다.')
        cases, mode = body.get('cases'), body.get('mode', 'scripted')
        if (not isinstance(cases, list) or not cases or any(type(c) is not str or c not in CASES for c in cases)
                or len(cases) != len(set(cases)) or mode not in ('scripted', 'openai')):
            raise ValueError('유효한 사례와 실행 방식을 선택하세요.')
        cases = [case for case in CASES if case in cases]
        with self.lock:
            if any(run['demo'].running() for run in self.runs.values()):
                raise RuntimeError('현재 실행이 끝난 뒤 새 실행을 시작하세요.')
            if mode == 'openai' and not self.live_readiness()['ready']:
                raise ValueError('실제 모델 연결 설정이 준비되지 않았습니다.')
            demo = Demo(cases)
            run_id = uuid.uuid4().hex
            if mode == 'scripted':
                demo.start_tasks()
            else:
                self._start_live(demo)
            self.runs[run_id] = dict(demo=demo, mode=mode)
        return self.snapshot(run_id)

    def _start_live(self, demo):
        """The user's run click selects the normal CLI's explicit paid path."""
        import atexit
        import subprocess
        demo._controls()
        output = demo.outputs['tasks']
        command = [str(ROOT / '.venv/bin/python'), '-B', '-m',
                   'examples.order_fulfillment.b_run', 'run', '--transport', 'openai',
                   '--cost-policy', str(self.policy_path), '--representative-max-cost-usd', '1.00',
                   '--output', str(output)]
        command += [part for case in demo.cases for part in ('--case', case)]
        with (demo.control / 'tasks.stdout.txt').open('xb') as stdout, \
                (demo.control / 'tasks.stderr.txt').open('xb') as stderr:
            demo.processes['tasks'] = subprocess.Popen(command, cwd=ROOT,
                env=clean_environment(demo.control), stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True)
        demo.started.add('tasks')
        atexit.register(demo._stop_at_exit, 'tasks')

    def snapshot(self, run_id):
        with self.lock:
            run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        demo, mode = run['demo'], run['mode']
        state = demo.status('tasks')
        output = demo.outputs['tasks']
        results = {}
        for case in demo.cases:
            path = output / 'campaign' / case / 'result.json'
            report = self._read_pending(path)
            if report is not None:
                results[case] = report
        summary = self._read_pending(output / 'campaign/summary.json')
        shutdown = self._read_pending(output / 'shutdown.json')
        validation = self._read_pending(output / ('model_validation.json' if mode == 'openai'
                                                 else 'task_validation.json'))
        passed = (state['exit_code'] == 0 and (summary or {}).get('passed') is True
                  and (shutdown or {}).get('server_stopped') is True
                  and (validation or {}).get('passed') is True)
        status = 'running' if state['running'] else 'passed' if passed else 'failed'
        stopped = self._read_pending(output / 'campaign/stopped.json')
        failure = self._read_pending(output / 'execution_failure.json')
        error = None
        if status == 'failed':
            # Do not echo logs or exception strings: they may contain credentials.
            error = dict(message='실행이 중단되었습니다. 자동 재실행하지 않습니다. 원문 결과와 종료 상태를 확인하세요.',
                         case=(stopped or {}).get('case'),
                         error_type=(failure or stopped or {}).get('error_type', 'RUN_FAILED'))
        attempts = max(sum(row.get('actual_api_attempts', 0) for row in results.values()),
                       (summary or {}).get('actual_api_attempts', 0),
                       (stopped or {}).get('actual_api_attempts', 0))
        if mode == 'openai':
            attempts = max(attempts, sum(len(list((output / 'campaign' / case).glob('request-*.json')))
                                         for case in demo.cases))
        return dict(id=run_id, mode=mode, status=status, cases=list(demo.cases), results=results,
                    summary=summary, shutdown=shutdown, error=error, stopped=stopped,
                    output=str(output), actual_api_attempts=attempts)

    @staticmethod
    def _read_pending(path):
        if not path.exists():
            return None
        try:
            return _read(path)
        except json.JSONDecodeError:
            return None  # The existing CLI publishes reports while the UI polls.

    def close(self):
        for run in self.runs.values():
            if run['demo'].running():
                run['demo'].stop('tasks')


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, lab):
        super().__init__(('127.0.0.1', port), Handler)
        self.lab = lab
        self.origin = 'http://127.0.0.1:' + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data, mime='application/json; charset=utf-8'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def trusted(self):
        return (self.headers.get('Host') == self.server.origin.removeprefix('http://')
                and self.headers.get('Origin', self.server.origin) == self.server.origin
                and self.headers.get('Sec-Fetch-Site') not in ('cross-site',))

    def do_GET(self):
        if not self.trusted():
            return self.respond(403, dict(error='동일한 로컬 주소에서 접속하세요.'))
        path = urlsplit(self.path).path
        try:
            if path == '/api/catalog':
                return self.respond(200, self.server.lab.catalog())
            if path.startswith('/api/runs/'):
                return self.respond(200, self.server.lab.snapshot(path.removeprefix('/api/runs/')))
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/index.html': ('index.html', 'text/html; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
            if path in assets:
                filename, mime = assets[path]
                return self.respond(200, (STATIC / filename).read_bytes(), mime)
            return self.respond(404, dict(error='페이지를 찾을 수 없습니다.'))
        except KeyError:
            return self.respond(404, dict(error='이 서버에서 시작한 실행이 아닙니다.'))
        except (ValueError, OSError, TypeError):
            return self.respond(500, dict(error='자료를 읽지 못했습니다. 서버 설정을 확인하세요.'))

    def do_POST(self):
        if (not self.trusted() or not secrets.compare_digest(
                self.headers.get('X-Ontol-Token', '').encode(), self.server.lab.csrf_token.encode())):
            return self.respond(403, dict(error='페이지를 새로고침한 뒤 실행하세요.'))
        if self.path != '/api/runs':
            return self.respond(404, dict(error='지원하지 않는 실행 경로입니다.'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get('Content-Type') != 'application/json':
                return self.respond(400, dict(error='작은 JSON 실행 요청이 필요합니다.'))
            body = json.loads(self.rfile.read(length))
            return self.respond(202, self.server.lab.start(body))
        except RuntimeError:
            return self.respond(409, dict(error='현재 실행이 끝난 뒤 새 실행을 시작하세요.'))
        except (ValueError, TypeError, OSError):
            return self.respond(400, dict(error='실행 설정을 확인하세요. 사례와 모델 연결 설정이 필요합니다.'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--cost-policy', help='existing operator-owned public cost JSON; enables real LLM')
    args = parser.parse_args(argv)
    lab = Lab(args.cost_policy)
    server = Server(args.port, lab)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print('B Workflow Lab: ' + server.origin, flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        lab.close()


if __name__ == '__main__':
    main()
