"""
Persistent state for GitHub Actions runners.

Runners are ephemeral, so deduplication state is persisted through a
workflow artifact named `dedup_state.json`. The agent downloads the
previous artifact when present, updates it, and uploads the new version
alongside reports. This is a conservative, documented approach with
minimal permissions.

State files are kept small and free of secrets.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ARTIFACT_NAME = "dedup_state"
STATE_FILENAME = "dedup_state.json"
STATE_VERSION = "1.2"
MAX_ENTRIES = 200
STALE_DAYS = 7


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seen": {},
    }


def load_state_from_artifact() -> Dict[str, Any]:
    """Load persistent state from the previous run's artifact.

    In GitHub Actions, this file is created by downloading the
    `dedup_state` artifact before the job runs. Locally, it is an
    optional file in the working directory.
    """
    # GitHub Actions workflow_dispatch/schedule jobs download artifacts
    # into the workspace. We check a small set of likely locations.
    candidates: List[Path] = [
        Path(STATE_FILENAME),
        Path("artifacts") / STATE_FILENAME,
        Path(".") / STATE_FILENAME,
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return _sanitize_state(data)
            except Exception:
                continue
    return default_state()


def _sanitize_state(data: Any) -> Dict[str, Any]:
    """Reject unexpected shapes and strip anything that is not a plain
    dict of strings/numbers."""
    if not isinstance(data, dict):
        return default_state()
    seen = data.get("seen")
    if isinstance(seen, dict):
        sanitized_seen: Dict[str, Any] = {}
        for key, value in seen.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, dict):
                sanitized_seen[key] = {
                    k: v for k, v in value.items()
                    if isinstance(k, str) and isinstance(v, (str, int, float, bool))
                }
        data = {
            "version": STATE_VERSION,
            "updated_at": data.get("updated_at", ""),
            "seen": sanitized_seen,
        }
    return data


def save_state_locally(state: Dict[str, Any]) -> Path:
    """Persist state to a local file for the upload-artifact step.

    In the workflow, this file is then uploaded as the `dedup_state`
    artifact for the next run.
    """
    path = Path(STATE_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    return path
