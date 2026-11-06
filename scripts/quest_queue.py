#!/usr/bin/env python3
"""Inspect the queue or record a human decision. Never performs external actions."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from quests import record_task_action, update_queue, review_candidate
from diagnostics import review_row
from state import load_state_from_artifact, save_state_locally


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'review', 'approve', 'complete'])
    parser.add_argument('--decision', choices=['accept', 'reject', 'defer'], help='Explicit candidate review; never overrides missing facts or approves a task')
    parser.add_argument('--reason', default='', help='Reason for the review decision')
    parser.add_argument('--reviewer', default='operator', help='Reviewer identifier, no personal information required')
    parser.add_argument('--submit-url', help='Official URL request; no facts are approved by submission')
    parser.add_argument('--revalidate', action='store_true', help='Independently check approved official transports; respect cache/backoff')
    parser.add_argument('--worklist', action='store_true', help='Read-only prioritized review worklist with revision tokens')
    parser.add_argument('--quest')
    parser.add_argument('--task')
    parser.add_argument('--evidence', help='Human decision/completion note, never private information or secrets')
    args = parser.parse_args()
    if (args.decision or args.submit_url or args.revalidate) and args.action != 'review':
        parser.error('--decision is only valid with review')
    state = load_state_from_artifact(read_only=args.action in ('list', 'review') and not (args.decision or args.submit_url or args.revalidate))
    update_queue(state, [])
    if args.action == 'list':
        print(json.dumps(state.get('quest_queue', {}), indent=2, ensure_ascii=False))
        return 0
    if args.action == 'review':
        from recovery import submit_url, revalidate
        if args.quest and args.quest not in state.get("quest_queue", {}):
            parser.error("Unknown quest ID")
        if args.submit_url:
            if not args.quest or args.quest not in state.get('quest_queue', {}):
                parser.error('--submit-url requires an existing --quest ID')
            try:
                submit_url(state['quest_queue'][args.quest], args.submit_url)
            except ValueError as exc:
                parser.error(str(exc))
            save_state_locally(state)
        if args.revalidate:
            from config_loader import effective_sources, load_config
            revalidate(state, effective_sources(), load_config(), candidate_ids={args.quest} if args.quest else None)
            save_state_locally(state)
        if args.decision:
            if not args.quest or not args.evidence:
                parser.error('--quest and --evidence are required for a review decision')
            try:
                review_candidate(state, args.quest, args.decision, args.evidence, reason=args.reason, reviewer=args.reviewer)
            except (KeyError, ValueError) as exc:
                parser.error(str(exc))
            save_state_locally(state)
        records = state.get('quest_queue', {})
        if args.quest:
            if args.quest not in records:
                parser.error('Unknown quest ID')
            records = {args.quest: records[args.quest]}
        if args.worklist:
            from triage import worklist
            print(json.dumps(worklist(records), indent=2, ensure_ascii=False))
            return 0
        print(json.dumps([review_row(q) for q in records.values()], indent=2, ensure_ascii=False))
        return 0
    if not all((args.quest, args.task, args.evidence)):
        parser.error('--quest, --task and --evidence are required')
    try:
        record_task_action(state, args.quest, args.task, args.action, args.evidence)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    save_state_locally(state)
    print('Human decision saved; no external action executed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
