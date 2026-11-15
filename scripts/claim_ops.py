#!/usr/bin/env python3
"""Cloud claim control. Production writes only under a serialized GitHub workflow.

Claim requests are provider-API submissions, not wallet receipts. No signing.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from claim_engine import (adapters, claim_report, authorize, revoke, eligible_to_dispatch,
                          verify_entitlement, submit_claim, utcnow)
from state import load_state_from_artifact, state_lock


def atomic_save(state, path=Path("dedup_state.json")):
    """Call only while holding the strict lock; never silently reset state."""
    fd, temp = tempfile.mkstemp(prefix="claim-state-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def restored_state():
    source = os.getenv("LUMAVUN_STATE_SOURCE", "")
    if not re.fullmatch(r"run:\d+", source):
        raise ValueError("Claim mutations require successfully restored production run state")
    path = Path("dedup_state.json")
    if not path.is_file() or not path.stat().st_size:
        raise ValueError("Refusing claim changes without persisted production state")
    with path.open() as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict) or not isinstance(raw.get("quest_queue"), dict):
        raise ValueError("Invalid persisted production quest queue")
    state = load_state_from_artifact(read_only=True)
    if len(state["quest_queue"]) != len(raw["quest_queue"]):
        raise ValueError("State migration would lose quest records; abort claim")
    return state


def perform_manual(state, registry, action):
    if os.getenv("CLAIM_WORKFLOW_AUTHORIZED") != "true":
        raise ValueError("Authenticated GitHub workflow dispatch required")
    ident = os.getenv("CLAIM_QUEST_ID", "")
    actor = os.getenv("CLAIM_ACTOR", "")
    request_id = os.getenv("CLAIM_REQUEST_ID", "")
    record = state["quest_queue"].get(ident)
    if not record:
        raise ValueError("Unknown persisted campaign ID")
    if action == "authorize":
        adapter = registry.get(ident)
        if not adapter:
            raise ValueError("No independently vetted, permitted official claim API adapter")
        event = authorize(record, adapter, wallet=os.getenv("PHANTOM_PUBLIC_ADDRESS", ""),
                          actor=actor, expected_revision=os.getenv("CLAIM_EXPECTED_REVISION", ""),
                          request_id=request_id)
    else:
        event = revoke(record, actor=actor, request_id=request_id)
    atomic_save(state)
    return {"campaign_id": ident, "event": event["kind"], "id": event["id"]}


def perform_run(state, registry):
    if os.getenv("CLAIM_EXECUTION_MODE") != "execute":
        return {"submitted": 0, "preflight_failures": 0, "mode": "audit-only"}
    if os.getenv("CLAIM_WORKFLOW_AUTHORIZED") != "true" and os.getenv("CLAIM_AUTO_ENABLED") != "true":
        raise ValueError("Claim execution requires authenticated dispatch or explicit auto mode")
    import requests
    wallet = os.getenv("PHANTOM_PUBLIC_ADDRESS", "")
    only = os.getenv("CLAIM_QUEST_ID", "") if os.getenv("CLAIM_WORKFLOW_AUTHORIZED") == "true" else ""
    attempted = 0
    refused = 0
    for ident, adapter in sorted(registry.items()):
        if only and ident != only:
            continue
        record = state["quest_queue"].get(ident)
        if not record or eligible_to_dispatch(record, adapter, wallet):
            continue
        try:
            verify_entitlement(adapter, record, wallet=wallet, session=requests,
                               token=os.getenv("CLAIM_PROVIDER_TOKEN", ""))
        except (requests.RequestException, ValueError):
            refused += 1
            continue
        # This one-way marker is written BEFORE POST. If POST or runner crashes,
        # do not replay an ambiguous financial/participation action.
        run_id = os.getenv("GITHUB_RUN_ID", "manual")
        attempt_id = f"claim-{run_id}-{ident[:16]}"
        at = utcnow().isoformat()
        record.setdefault("operator_history", []).append({
            "id": attempt_id, "at": at, "kind": "CLAIM_DISPATCHED",
            "adapter": adapter["id"],
            "note": "Durable one-time claim dispatch claim; not a payout",
        })
        atomic_save(state)
        try:
            outcome = submit_claim(adapter, record, wallet=wallet, request_id=attempt_id,
                                   session=requests, token=os.getenv("CLAIM_PROVIDER_TOKEN", ""))
        except (requests.RequestException, ValueError):
            outcome = {"status": "CLAIM_SUBMISSION_UNKNOWN",
                       "note": "Possible submission; no automatic retry or earnings claim"}
        record.setdefault("operator_history", []).append({
            "id": attempt_id+"-result", "at": utcnow().isoformat(),
            "kind": "CLAIM_RESULT", "status": outcome["status"],
            "provider_claim_id": outcome.get("claim_id"),
            "note": outcome["note"],
        })
        atomic_save(state)
        attempted += 1
    return {"submitted_or_ambiguous": attempted, "preflight_failures": refused, "mode": "execute"}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("audit", "authorize", "revoke", "run"))
    args = cli.parse_args()
    # No adapter is trusted merely because it was suggested by a discovery feed.
    registry = adapters()
    with state_lock(strict=True):
        state = restored_state()
        if args.action in ("authorize", "revoke"):
            outcome = perform_manual(state, registry, args.action)
        elif args.action == "run":
            outcome = perform_run(state, registry)
        else:
            outcome = {"mode": "audit-only"}
        report = claim_report(state, registry, wallet=os.getenv("PHANTOM_PUBLIC_ADDRESS", ""))
        report["execution"] = outcome
        Path("claim_readiness.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    # Do not print sensitive wallet identifiers, credentials or input URLs.
    print(json.dumps({"ready": report["ready"], "attempted": report["attempted"],
                      "reviewed_claim_adapters": len(registry), "execution": outcome}))


if __name__ == "__main__":
    main()
