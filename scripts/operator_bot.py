#!/usr/bin/env python3
"""One bounded read-only Telegram poll; existing environment allowlist is mandatory."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from operator_bot import poll, claim_update
from state import load_state_from_artifact

if __name__ == '__main__':
    print(json.dumps(poll(load_state_from_artifact(read_only=True), checkpoint=claim_update)))
