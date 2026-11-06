#!/usr/bin/env python3
"""Inspect playbooks or explicitly record human operations. Never submits anything."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from operations import playbook, transition, save_grant_plan
from state import load_state_from_artifact, state_lock


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['inspect', 'transition', 'plan', 'workflow'])
    p.add_argument('--quest')
    p.add_argument('--target')
    p.add_argument('--reason')
    p.add_argument('--actor')
    p.add_argument('--revision')
    p.add_argument('--request-id')
    p.add_argument('--authorize', action='store_true')
    p.add_argument('--plan-file')
    args = p.parse_args()
    if args.action == 'inspect':
        state = load_state_from_artifact(read_only=True)
        print(json.dumps([playbook(q) for cid,q in state['quest_queue'].items() if not args.quest or cid == args.quest], indent=2))
        return
    plan = None
    if args.action == 'workflow':
        if not re.fullmatch(r'run:\d+', os.environ.get('LUMAVUN_STATE_SOURCE', '')):
            raise ValueError('Production state restore required')
        args.quest, args.target = os.getenv('QUEST_ID'), os.getenv('OPERATION')
        args.reason, args.actor = os.getenv('TASK_EVIDENCE'), os.getenv('REVIEWER')
        args.revision, args.request_id = os.getenv('EXPECTED_REVISION'), os.getenv('REVIEW_REQUEST_ID')
        # This entry point runs only for an explicit authenticated workflow dispatch.
        args.authorize = os.getenv('OPERATOR_AUTHORIZED') == 'true'
        if args.target == 'GRANT_PLAN':
            plan = json.loads(os.environ['GRANT_PLAN'])
    elif args.action == 'plan':
        plan = json.loads(Path(args.plan_file).read_text())
    with state_lock(strict=True):
        path = Path('dedup_state.json')
        raw = json.loads(path.read_text())
        if not isinstance(raw.get('quest_queue'), dict):
            raise ValueError('Invalid state; refusing mutation')
        state = load_state_from_artifact(read_only=True)
        record = state['quest_queue'][args.quest]
        kwargs = dict(authorized=args.authorize, actor=args.actor, reason=args.reason,
                      expected_revision=args.revision, request_id=args.request_id)
        result = save_grant_plan(record, plan, **kwargs) if plan is not None else transition(record, args.target, **kwargs)
        tmp = path.with_suffix('.ops.tmp')
        with tmp.open('w') as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    print(json.dumps({'request_id': result['id'], 'status': result['new_status']}))


if __name__ == '__main__':
    main()
