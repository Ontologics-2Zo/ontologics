"""B execution: run a selected CQ, or explicitly choose the optional demo flow.

python -m examples.order_fulfillment.b_run tasks --output /absolute/new/run
The tasks command validates the new unregistered-input roles with free doubles.
Legacy free/live storage commands are retired before resource preparation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .b_run_isolation import (LEGACY_EXECUTION_MESSAGE, checked_path, launch, postgres_bin, prepare)
from .b_run_transport import CASES
from ontologics.service import DomainError


def read_json(path: str):
    value = checked_path(Path(path))
    if value.suffix != '.json' or value.stat().st_size > 256_000:
        raise ValueError('bounded explicit JSON file required')
    return json.loads(value.read_text(encoding='utf-8'))


def _launch_model(settings, approval):
    from .b_run_isolation import save
    output = Path(settings['output'])
    save(output / 'authorization.json', approval)
    if settings['mode'] == 'live':
        # Resolve only after explicit command/interactive execution consent.
        import socket
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(
            'api.openai.com', 443, socket.AF_INET, socket.SOCK_STREAM)})
        if not addresses:
            raise ValueError('No endpoint addresses; no run started')
        save(output / 'endpoint_ips.json', addresses)
    return launch(settings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='tasks', choices=(
        'run', 'tasks', 'preflight', 'plan-model', 'run-model', 'free', 'live', 'list', 'status'))
    parser.add_argument('--output', help='absolute NEW output directory; never reused')
    parser.add_argument('--case', action='append', choices=CASES,
                        help='repeat in fixed order; preflight/plan-model default CQ01 intake, tasks default ten')
    parser.add_argument('--pg-bin', help='PostgreSQL with pgvector bin directory')
    parser.add_argument('--authorization', help='retired legacy option; rejected')
    parser.add_argument('--free-gate', help='retired legacy option; rejected')
    parser.add_argument('--cost-policy', help='explicit public common cost JSON; run/preflight/plan-model, no key')
    parser.add_argument('--input', help='run only: explicit synthetic contract/operations Markdown under data/order_fulfillment/')
    parser.add_argument('--preflight', help='completed matching protected preflight directory')
    parser.add_argument('--skip-preflight', action='store_true',
                        help='plan-model AI technical mode only; explicitly omit B free preflight')
    parser.add_argument('--transport', choices=('mock_http', 'openai'),
                        help='run/plan-model; default mock_http, openai explicitly starts paid execution')
    parser.add_argument('--after', help='completed reviewed CQ01 run; required for the live remaining nine')
    parser.add_argument('--review-mode', choices=('human', 'ai_semantic'), help='plan-model only')
    parser.add_argument('--representative-max-cost-usd',
                        help='run or plan-model AI technical mode; positive cap at most 1.00 (default 1.00)')
    args = parser.parse_args(argv)
    if args.input and args.command != 'run':
        parser.error('--input is supported only by run')
    if args.command == 'list':
        print('\n'.join(CASES)); return 0
    if args.command in ('free', 'live'):
        print(LEGACY_EXECUTION_MESSAGE, file=sys.stderr)
        return 1
    if not args.output:
        parser.error('--output is required')
    try:
        output = checked_path(Path(args.output))
        if args.command == 'status':
            print(json.dumps({name: read_json(str(output / name))
                              for name in ('shutdown.json', 'completion.json', 'free_gate.json', 'task_validation.json',
                                           'preflight_validation.json', 'authorization_pending.json',
                                           'model_validation.json', 'launch_claim.json',
                                           'execution_failure.json', 'campaign_failure.json')
                              if (output / name).is_file()}, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'run-model':
            if any((args.case, args.preflight, args.skip_preflight, args.cost_policy, args.transport, args.after, args.review_mode,
                    args.representative_max_cost_usd is not None,
                    args.authorization, args.free_gate, args.pg_bin)):
                raise ValueError('Run the exact preserved plan without configuration overrides')
            from . import b_model_run
            from .b_run_isolation import require_input_tasks, save
            settings = read_json(str(output / 'settings.json'))
            require_input_tasks(settings)
            if settings.get('workflow') != 'input_model' or settings.get('output') != str(output):
                raise ValueError('Select the exact newly prepared model run')
            if (output / 'authorization.json').exists() or (output / 'launch_claim.json').exists():
                raise ValueError('A confirmed or started run cannot be repeated; create a new plan')
            approval = b_model_run.confirm(settings['model_run'])
            return _launch_model(settings, approval)
        cases = list(args.case or (['cq01_intake'] if args.command in ('preflight', 'plan-model') else CASES))
        if len(set(cases)) != len(cases) or cases != [case for case in CASES if case in cases]:
            raise ValueError('cases must be unique and in the fixed CQ order')
        if args.authorization or args.free_gate:
            raise ValueError('input tasks do not accept paid authorization')
        if args.command == 'run':
            if (not args.case or not args.cost_policy or args.preflight or args.skip_preflight
                    or args.after or args.review_mode):
                raise ValueError('run requires explicit cases/cost policy and accepts no demo options')
            from . import b_model_run
            from .b_model_preflight import load_policy
            from .b_run_isolation import save
            from .b_terminal_input import load_input
            plan = b_model_run.build_plan(output=output, cases=cases,
                policy_snapshot=load_policy(args.cost_policy), transport=args.transport or 'mock_http',
                review_mode='none', representative_max_cost_usd=args.representative_max_cost_usd or '1.00',
                terminal_input=load_input(args.input) if args.input else None)
            if args.input:
                print('이번 입력: ' + plan['terminal_input']['path'], flush=True)
            print(json.dumps(dict(execution='normal', transport=plan['transport'], cases=cases,
                model=plan['profile']['provider_config'], limits=plan['profile']['root_limits'],
                cost_policy=plan['profile']['cost_policy'],
                representative_max_cost_usd=plan['representative_max_cost_usd'],
                human_review='not_performed', business_approval='not_performed'), ensure_ascii=False), flush=True)
            settings = prepare(output, mode='live' if plan['transport'] == 'openai' else 'free',
                workflow='input_model', cases=cases, pg_bin=postgres_bin(args.pg_bin), model_run=plan)
            save(output / 'model_plan.json', plan)
            if args.input:
                (output / 'input_source.md').write_text(plan['terminal_input']['text'], encoding='utf-8')
            approval = b_model_run.authorization(plan, approver='cli-operator', confirmed=True)
            return _launch_model(settings, approval)
        options = {}
        if args.command == 'plan-model':
            if not args.cost_policy or bool(args.preflight) == args.skip_preflight:
                raise ValueError('Select matching preflight or explicit omission, and public cost policy')
            from .b_model_preflight import load_policy
            from .b_model_run import build_plan
            from .b_run_isolation import save
            if args.representative_max_cost_usd is not None:
                if args.review_mode != 'ai_semantic':
                    raise ValueError('Representative cap overrides require explicit AI technical review mode')
                options['representative_max_cost_usd'] = args.representative_max_cost_usd
            plan = build_plan(preflight=Path(args.preflight) if args.preflight else None,
                skip_preflight=args.skip_preflight, output=output, cases=cases,
                policy_snapshot=load_policy(args.cost_policy), transport=args.transport or 'mock_http',
                after=Path(args.after) if args.after else None, review_mode=args.review_mode or 'human',
                **options)
            settings = prepare(output, mode='live' if plan['transport'] == 'openai' else 'free',
                workflow='input_model', cases=cases, pg_bin=postgres_bin(args.pg_bin), model_run=plan)
            save(output / 'model_plan.json', plan)
            print(json.dumps(dict(output=str(output), status='awaiting_execution_approval',
                transport=plan['transport'], cases=cases, approved=False, actual_api_attempts=0), ensure_ascii=False))
            return 0
        if (args.preflight or args.skip_preflight or args.transport or args.after or args.review_mode
                or args.representative_max_cost_usd is not None):
            raise ValueError('Model plan options are only available to plan-model')
        if args.command == 'preflight':
            if not args.cost_policy:
                raise ValueError('preflight requires explicit public common cost settings')
            from .b_model_preflight import load_policy
            options['policy_snapshot'] = load_policy(args.cost_policy)
        elif args.cost_policy:
            raise ValueError('cost policy is used only by preflight')
        settings = prepare(output, mode='free', workflow='input_tasks', cases=cases,
                           pg_bin=postgres_bin(args.pg_bin), **options)
        code = launch(settings)
        print(json.dumps(dict(output=str(output), mode=args.command, exit_code=code), ensure_ascii=False))
        return code
    except (ValueError, OSError, RuntimeError, DomainError, EOFError, KeyboardInterrupt, KeyError, TypeError) as error:
        # Never interpolate exception messages: loader/network errors can contain secrets.
        print('B run rejected/stopped: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
