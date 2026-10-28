"""
Persistent state for GitHub Actions runners (V1.3).

Runners are ephemeral, so state is persisted through a workflow artifact
named ``dedup_state.json``. The workflow restores the artifact from the
previous successful run, the agent updates it, and the new version is
uploaded again.

What is stored:

* ``seen``          - one record per campaign id, with first/last sighting.
* ``notifications`` - which campaign ids have already been sent to Telegram.
* ``runs``          - how many runs have contributed to this state.

What is never stored: bot tokens, chat ids, wallet secrets, seed phrases,
raw feed bodies. Everything is plain JSON of strings and numbers.

Robustness choices that matter in CI:

* Writes are atomic (temp file + ``os.replace``), so a killed job cannot
  truncate the state.
* A corrupt state file is moved aside to ``dedup_state.corrupt.json`` and
  the run continues with fresh state instead of crashing.
* A lock file serializes concurrent runs, and saving merges with whatever
  is already on disk, so two overlapping runs do not lose each other's
  history.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

ARTIFACT_NAME = "dedup_state"
STATE_FILENAME = "dedup_state.json"
CORRUPT_FILENAME = "dedup_state.corrupt.json"
LOCK_FILENAME = "dedup_state.lock"
STATE_VERSION = "1.3"
MAX_ENTRIES = 200
STALE_DAYS = 7
LOCK_TIMEOUT_SECONDS = 30.0
LOCK_STALE_SECONDS = 120.0

_MAX_NOTIFICATIONS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "updated_at": _now_iso(),
        "runs": 0,
        "seen": {},
        "notifications": {},
    }


def _candidate_paths() -> List[Path]:
    return [
        Path(STATE_FILENAME),
        Path("artifacts") / STATE_FILENAME,
        Path(".") / STATE_FILENAME,
    ]


def load_state_from_artifact() -> Dict[str, Any]:
    """Load persistent state from the previous run's artifact.

    In GitHub Actions the file is placed in the workspace by the restore
    step before the agent runs. Locally it is an optional file.
    """
    for path in _candidate_paths():
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            _quarantine(path)
            return default_state()
        if not isinstance(data, dict):
            _quarantine(path)
            return default_state()
        return _sanitize_state(data)
    return default_state()


def _quarantine(path: Path) -> None:
    """Move a corrupt state file aside instead of deleting the evidence."""
    try:
        os.replace(path, Path(CORRUPT_FILENAME))
    except Exception:
        try:
            path.unlink()
        except Exception:
            pass


def _sanitize_state(data: Any) -> Dict[str, Any]:
    """Reject unexpected shapes and strip anything that is not a plain
    dict of strings/numbers."""
    if not isinstance(data, dict):
        return default_state()

    clean: Dict[str, Any] = {
        "version": STATE_VERSION,
        "updated_at": str(data.get("updated_at", "") or ""),
        "runs": 0,
        "seen": {},
        "notifications": {},
    }
    runs = data.get("runs", 0)
    if isinstance(runs, int) and runs >= 0:
        clean["runs"] = runs

    seen = data.get("seen")
    if isinstance(seen, dict):
        for key, value in seen.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(value, dict):
                clean["seen"][key] = {
                    k: v
                    for k, v in value.items()
                    if isinstance(k, str) and isinstance(v, (str, int, float, bool))
                }

    notifications = data.get("notifications")
    if isinstance(notifications, dict):
        for key, value in notifications.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(value, dict):
                clean["notifications"][key] = {
                    k: v
                    for k, v in value.items()
                    if isinstance(k, str) and isinstance(v, (str, int, float, bool))
                }

    return clean


def merge_states(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """Union two states without losing history from either.

    Used when saving over an existing file: two overlapping runs (or an
    old artifact plus this run) must not erase each other's sightings.
    """
    merged = default_state()
    merged["runs"] = max(int(base.get("runs", 0) or 0), int(update.get("runs", 0) or 0))
    merged["updated_at"] = (
        max(
            str(base.get("updated_at", "") or ""),
            str(update.get("updated_at", "") or ""),
        )
        or _now_iso()
    )

    seen: Dict[str, Dict[str, Any]] = {}
    for source in (base.get("seen") or {}, update.get("seen") or {}):
        for key, record in source.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            existing = seen.get(key)
            if not existing:
                seen[key] = dict(record)
                continue
            existing_last = str(existing.get("last_seen", "") or "")
            record_last = str(record.get("last_seen", "") or "")
            first_values = [
                v
                for v in (
                    str(existing.get("first_seen", "") or ""),
                    str(record.get("first_seen", "") or ""),
                )
                if v
            ]
            winner = record if record_last >= existing_last else existing
            merged_record = dict(winner)
            if first_values:
                merged_record["first_seen"] = min(first_values)
            merged_record["notified"] = bool(
                existing.get("notified", False) or record.get("notified", False)
            )
            seen[key] = merged_record
    merged["seen"] = seen

    notifications: Dict[str, Dict[str, Any]] = {}
    for source in (base.get("notifications") or {}, update.get("notifications") or {}):
        for key, record in source.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            existing = notifications.get(key)
            if not existing:
                notifications[key] = dict(record)
                continue
            keep = (
                record
                if str(record.get("sent_at", "")) >= str(existing.get("sent_at", ""))
                else existing
            )
            notifications[key] = dict(keep)
    if len(notifications) > _MAX_NOTIFICATIONS:
        ordered = sorted(
            notifications.items(), key=lambda item: str(item[1].get("sent_at", ""))
        )
        notifications = dict(ordered[-_MAX_NOTIFICATIONS:])
    merged["notifications"] = notifications
    return merged


@contextmanager
def state_lock(
    timeout: float = LOCK_TIMEOUT_SECONDS,
    stale_after: float = LOCK_STALE_SECONDS,
) -> Iterator[None]:
    """Serialize state writes across concurrent runs.

    If the lock cannot be taken within ``timeout`` the write proceeds
    anyway: losing a lock is better than hanging a CI job forever, and the
    merge step still protects existing history.
    """
    lock_path = Path(LOCK_FILENAME)
    deadline = time.monotonic() + timeout
    fd: Optional[int] = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode("ascii"))
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_after:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def save_state_locally(state: Dict[str, Any]) -> Path:
    """Persist state atomically, merging with any state already on disk."""
    path = Path(STATE_FILENAME)
    with state_lock():
        existing = load_state_from_artifact()
        payload = merge_states(existing, state)
        payload["version"] = STATE_VERSION
        if not payload.get("updated_at"):
            payload["updated_at"] = _now_iso()
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    return path


def record_notification(
    state: Dict[str, Any], campaign_id: str, **extra: Any
) -> Dict[str, Any]:
    """Record that a campaign was notified, so later runs stay quiet."""
    notifications = dict(state.get("notifications") or {})
    record: Dict[str, Any] = {"sent_at": _now_iso(), "kind": "opportunity"}
    for key, value in extra.items():
        if isinstance(value, (str, int, float, bool)):
            record[key] = value
    notifications[campaign_id] = record
    state["notifications"] = notifications
    seen = dict(state.get("seen") or {})
    if campaign_id in seen and isinstance(seen[campaign_id], dict):
        updated = dict(seen[campaign_id])
        updated["notified"] = True
        seen[campaign_id] = updated
        state["seen"] = seen
    return state


def was_notified(state: Dict[str, Any], campaign_id: str) -> bool:
    notifications = state.get("notifications") or {}
    return campaign_id in notifications


def mark_run(state: Dict[str, Any]) -> Dict[str, Any]:
    """Increment the run counter contribution for this run."""
    state["runs"] = int(state.get("runs", 0) or 0) + 1
    state["version"] = STATE_VERSION
    state["updated_at"] = _now_iso()
    return state
