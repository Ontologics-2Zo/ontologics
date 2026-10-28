"""Reusable protected B representative runner; free HTTP doubles are the default.

python -m examples.order_fulfillment.b_run free --output /absolute/new/run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .b_run_isolation import checked_path, launch, postgres_bin, prepare, sha
from .b_run_transport import CASES


def read_json(path: str):
    value = checked_path(Path(path))
    if value.suffix != '.json' or value.stat().st_size > 256_000:
        raise ValueError('bounded explicit JSON file required')
    return json.loads(value.read_text(encoding='utf-8'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='free', choices=('free', 'live', 'list', 'status'))
    parser.add_argument('--output', help='absolute NEW output directory; never reused')
    parser.add_argument('--case', action='append', choices=CASES,
                        help='repeat in the fixed order; default all ten')
    parser.add_argument('--pg-bin', help='PostgreSQL with pgvector bin directory')
    parser.add_argument('--authorization', help='new explicit run-bound approval JSON, live only')
    parser.add_argument('--free-gate', help='matching completed reusable free run/free_gate.json, live only')
    args = parser.parse_args(argv)
    if args.command == 'list':
        print('\n'.join(CASES)); return 0
    if not args.output:
        parser.error('--output is required')
    try:
        output = checked_path(Path(args.output))
        if args.command == 'status':
            print(json.dumps({name: read_json(str(output / name))
                              for name in ('shutdown.json', 'completion.json', 'free_gate.json',
                                           'execution_failure.json', 'campaign_failure.json')
                              if (output / name).is_file()}, ensure_ascii=False, indent=2))
            return 0
        cases = list(args.case or CASES)
        if len(set(cases)) != len(cases) or cases != [case for case in CASES if case in cases]:
            raise ValueError('cases must be unique and in the fixed CQ order')
        if args.command == 'free' and (args.authorization or args.free_gate):
            raise ValueError('free mode does not accept paid authorization')
        if args.command == 'live' and not (args.authorization and args.free_gate):
            raise ValueError('live requires NEW explicit authorization and matching free gate')
        auth = read_json(args.authorization) if args.authorization else None
        gate = read_json(args.free_gate) if args.free_gate else None
        if gate is not None:
            gate_dir = Path(args.free_gate).parent
            if (sha(gate_dir / 'campaign/summary.json') != gate.get('summary_sha256') or
                    sha(gate_dir / 'shutdown.json') != gate.get('shutdown_sha256') or
                    read_json(str(gate_dir / 'shutdown.json')).get('server_stopped') is not True):
                raise ValueError('free gate evidence no longer matches')
        settings = prepare(output, mode=args.command, cases=cases, pg_bin=postgres_bin(args.pg_bin),
                           authorization=auth, gate=gate,
                           gate_hash=sha(Path(args.free_gate)) if args.free_gate else None)
        code = launch(settings)
        print(json.dumps(dict(output=str(output), mode=args.command, exit_code=code), ensure_ascii=False))
        return code
    except (ValueError, OSError, RuntimeError) as error:
        # Never interpolate exception messages: loader/network errors can contain secrets.
        print('B run rejected/stopped: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
