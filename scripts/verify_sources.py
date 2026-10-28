#!/usr/bin/env python3
"""
Verify every configured source endpoint (V1.3).

This is the operational companion to the agent: it reads the same registry,
performs the same HTTPS requests (robots-aware, rate-limited), and reports
what each source actually returned right now.

It writes ``source_health.json`` for the workflow artifact and prints a
table. Exit status is 1 when any enabled source fails, so a broken source is
visible - the workflow runs it with ``continue-on-error`` so the scan itself
still proceeds.

Usage:
    python scripts/verify_sources.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import effective_sources, load_config  # noqa: E402
from sources import fetch_source  # noqa: E402

OUTPUT = Path("source_health.json")


def _newest(result: Any) -> str:
    stamps = [item.published_ts for item in result.items if item.published_ts]
    if not stamps:
        return ""
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).isoformat()


def main() -> int:
    config = load_config()
    policy = config.get("source_policy", {}) or {}
    specs = effective_sources()

    print(f"LUMAVUN source verification - {len(specs)} enabled source(s)")
    print("")

    entries: List[Dict[str, Any]] = []
    failures = 0

    for spec in specs:
        result = fetch_source(
            spec,
            user_agent=str(policy.get("user_agent") or "LUMAVUN-Crypto-Agent/1.3"),
            timeout=int(policy.get("request_timeout_seconds", 20)),
            min_interval=float(policy.get("min_interval_seconds", 1.5)),
            respect_robots=bool(policy.get("respect_robots", True)),
        )
        ok = result.error is None
        if not ok:
            failures += 1
        record = {
            "id": spec.id,
            "url": spec.url,
            "kind": spec.kind,
            "parser": spec.parser,
            "category": spec.category,
            "official": spec.official,
            "status": "ok" if ok else "error",
            "http_status": result.http_status,
            "content_type": result.content_type,
            "entries": result.entries_seen,
            "newest_entry": _newest(result),
            "robots_blocked": result.robots_blocked,
            "error": result.error,
        }
        entries.append(record)

        status = "OK  " if ok else "FAIL"
        print(
            f"[{status}] {spec.id:<22} {spec.kind:<4} "
            f"{str(result.http_status or '-'):<4} entries={result.entries_seen:<3} "
            f"{result.content_type[:32]:<32}"
            + (f" error={result.error}" if result.error else "")
        )

    payload = {
        "agent": "LUMAVUN Crypto Agent V1.3",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "total": len(entries),
        "failed": failures,
        "sources": entries,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("")
    print(f"Wrote {OUTPUT} ({len(entries)} sources, {failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
