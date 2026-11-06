#!/usr/bin/env python3
"""Import a URL request, never operator-provided campaign facts."""
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from manual_evidence import apply_request
from state import state_lock, load_state_from_artifact


def main():
    if not re.fullmatch(r'run:\d+', os.environ.get('LUMAVUN_STATE_SOURCE', '')):
        raise ValueError('A successfully restored production state is required')
    path = Path('dedup_state.json')
    with state_lock(strict=True):
        # Parse before the permissive loader: malformed state must fail closed.
        raw = json.loads(path.read_text())
        if not isinstance(raw.get('quest_queue'), dict):
            raise ValueError('Invalid queue')
        state = load_state_from_artifact(read_only=True)
        result = apply_request(state, {k: os.environ.get(v, '') for k, v in {
            'campaign_id': 'QUEST_ID', 'url': 'EVIDENCE_URL', 'expected_revision': 'EXPECTED_REVISION',
            'request_id': 'REVIEW_REQUEST_ID', 'reviewer': 'REVIEWER'}.items()})
        tmp = path.with_suffix('.import.tmp')
        with tmp.open('w') as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
