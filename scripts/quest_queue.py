#!/usr/bin/env python3
"""Inspect the queue or record a human decision. Never performs external actions."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from quests import record_task_action, update_queue
from state import load_state_from_artifact, save_state_locally


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'approve', 'complete'])
    parser.add_argument('--quest')
    parser.add_argument('--task')
    parser.add_argument('--evidence', help='Human decision/completion note, never private information or secrets')
    args = parser.parse_args()
    state = load_state_from_artifact()
    update_queue(state, [])
    if args.action == 'list':
        print(json.dumps(state.get('quest_queue', {}), indent=2, ensure_ascii=False))
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
