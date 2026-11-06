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
    parser.add_argument('--quest')
    parser.add_argument('--task')
    parser.add_argument('--evidence', help='Human decision/completion note, never private information or secrets')
    args = parser.parse_args()
    if args.decision and args.action != 'review':
        parser.error('--decision is only valid with review')
    state = load_state_from_artifact(read_only=args.action in ('list', 'review') and not args.decision)
    update_queue(state, [])
    if args.action == 'list':
        print(json.dumps(state.get('quest_queue', {}), indent=2, ensure_ascii=False))
        return 0
    if args.action == 'review':
        if args.decision:
            if not args.quest or not args.evidence:
                parser.error('--quest and --evidence are required for a review decision')
            try:
                review_candidate(state, args.quest, args.decision, args.evidence)
            except (KeyError, ValueError) as exc:
                parser.error(str(exc))
            save_state_locally(state)
        records = state.get('quest_queue', {})
        if args.quest:
            if args.quest not in records:
                parser.error('Unknown quest ID')
            records = {args.quest: records[args.quest]}
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
