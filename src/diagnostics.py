"""Structured discovery decisions and explicitly scoped scan statistics."""
from __future__ import annotations

from datetime import datetime, timezone

REASONS = {
    "SOURCE_UNREACHABLE": ("Source could not be read", "Retry the authorized source; inspect HTTP/robots/rate-limit diagnostics"),
    "NEWS_ONLY": ("No supported actionable campaign was established", "Keep as information only; do not send an opportunity alert"),
    "MISSING_OFFICIAL_EVIDENCE": ("Official announcement evidence is missing", "Obtain a corroborating official feed or authorized API announcement"),
    "MISSING_REWARD_EVIDENCE": ("An identifiable reward program is not evidenced", "Obtain the organizer's explicit reward-program announcement"),
    "MISSING_PARTICIPATION_REQUIREMENTS": ("Participation tasks are not evidenced", "Obtain the official task requirements"),
    "MISSING_ORGANIZER": ("An attributable organizer is missing", "Identify the organizer from official evidence"),
    "MANDATORY_PAYMENT": ("Mandatory capital or payment is required", "Reject from the zero-capital queue"),
    "UNKNOWN_COSTS": ("Required fees are not fully established", "Obtain explicit fee evidence; do not approve UNKNOWN costs"),
    "EXPIRED_CAMPAIGN": ("Campaign has expired", "Do not act; await a separately verified new campaign"),
    "ELIGIBILITY_UNKNOWN": ("Eligibility has not been established", "Check the stated restrictions through an authorized read-only method"),
    "ELIGIBILITY_FAILED": ("Eligibility requirements are not met", "Do not act unless new verified eligibility evidence changes the result"),
    "DUPLICATE": ("Canonical campaign was observed before", "Suppress already delivered alerts; retry only eligible unsent alerts"),
    "AWAITING_HUMAN_REVIEW": ("A human decision is pending", "Review the evidence and approve specific tasks only after all gates pass"),
    "HUMAN_REJECTED": ("An explicit human review rejected this candidate", "Retain rejection until an explicit new review; do not override cost gates"),
    "HIGH_RISK": ("High-risk indicators were detected", "Do not treat this as actionable; review risk evidence"),
    "NOT_STARTED": ("Campaign has not started", "Monitor the verified start date"),
    "INVALID_DATE": ("A supplied campaign date is invalid", "Obtain a valid official date"),
    "STALE_EVIDENCE": ("Evidence is older than 24 hours", "Refresh the authorized source before approval"),
    "PLATFORM_UNAVAILABLE": ("Platform campaign is not accepting participation", "Await an officially active campaign"),
    "UNVERIFIED_CLASSIFICATION": ("Evidence does not support a verified campaign classification", "Review the quoted evidence without promoting speculation"),
}


def reason(code: str, detail: str | None = None) -> dict:
    label, action = REASONS[code]
    return {"code": code, "detail": detail or label, "next_action": action}


def candidate_reasons(record: dict, *, duplicate: bool = False, now: datetime | None = None) -> list[dict]:
    from quests import parse_time
    now = now or datetime.now(timezone.utc)
    checks = record.get("evidence_checks") or {}
    codes = []
    if record.get("campaign_type") == "NEWS_ONLY":
        codes.append("NEWS_ONLY")
    official = checks.get("official", bool(record.get("official_url") and record.get("source_verification_status") in ("official_feed", "authorized_galxe_api", "official_campaign_api")))
    reward = checks.get("reward", bool(record.get("evidence_verified")))
    if not official:
        codes.append("MISSING_OFFICIAL_EVIDENCE")
    if not reward:
        codes.append("MISSING_REWARD_EVIDENCE")
    if not record.get("tasks"):
        codes.append("MISSING_PARTICIPATION_REQUIREMENTS")
    if not record.get("project"):
        codes.append("MISSING_ORGANIZER")
    if record.get("cost_status") == "FAIL":
        codes.append("MANDATORY_PAYMENT")
    elif record.get("cost_status") != "PASS":
        codes.append("UNKNOWN_COSTS")
    end = parse_time(record.get("end_at"), end=True)
    start = parse_time(record.get("start_at"))
    if (end and end < now) or record.get("platform_status") == "Expired":
        codes.append("EXPIRED_CAMPAIGN")
    if (record.get("end_at") and end is None) or (record.get("start_at") and start is None):
        codes.append("INVALID_DATE")
    if (start and start > now) or record.get("platform_status") == "NotStarted":
        codes.append("NOT_STARTED")
    if record.get("eligibility_status") == "FAIL":
        codes.append("ELIGIBILITY_FAILED")
    elif record.get("eligibility_status") != "PASS":
        codes.append("ELIGIBILITY_UNKNOWN")
    if record.get("risk_band") == "high":
        codes.append("HIGH_RISK")
    if record.get("platform_status") in ("Deleted", "CapReached", "Draft"):
        codes.append("PLATFORM_UNAVAILABLE")
    if duplicate:
        codes.append("DUPLICATE")
    review = record.get("human_review") or {}
    if review.get("decision") == "reject":
        codes.append("HUMAN_REJECTED")
    if record.get("status") == "NEEDS_APPROVAL" or review.get("decision") == "defer":
        codes.append("AWAITING_HUMAN_REVIEW")
    observed = parse_time(record.get("last_observed"))
    if observed and (now - observed).total_seconds() > 86400:
        codes.append("STALE_EVIDENCE")
    if not record.get("evidence_verified") and record.get("campaign_type") != "NEWS_ONLY":
        codes.append("UNVERIFIED_CLASSIFICATION")
    return [reason(code) for code in dict.fromkeys(codes)]


def review_row(record: dict, *, duplicate: bool = False, now: datetime | None = None) -> dict:
    from qualification import qualify
    from recovery import gap_analysis
    qualification = qualify(record, now)
    reasons = candidate_reasons(record, duplicate=duplicate, now=now)
    return {
        "evidence_gap": gap_analysis(record, now),
        "evidence_requests": record.get("evidence_requests", []), "evidence_resolution": record.get("evidence_resolution", {}),
        "terms_events": record.get("terms_events", []),
        "qualification": qualification, "review_history": record.get("review_history", []),
        "id": record["id"], "name": record.get("name", ""), "project": record.get("project", ""),
        "source_url": record.get("source", ""), "announcement_url": record.get("announcement_url", ""),
        "official_url": record.get("official_url", ""), "evidence": record.get("evidence", []),
        "evidence_checks": record.get("evidence_checks", {}), "cost_status": record.get("cost_status", "UNKNOWN"),
        "cost_evidence": record.get("cost_evidence", ""), "status": record.get("status", "DISCOVERED"),
        "campaign_type": record.get("campaign_type"), "reasons": reasons,
        "next_actions": list(dict.fromkeys(r["next_action"] for r in reasons)) or ["Monitor; no automated external task execution"],
        "human_review": record.get("human_review"),
    }


def discovery_report(observations: list[dict], source_health: list, queue: dict, previous_ids: set[str],
                     rewards: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows, encountered = [], set(previous_ids)
    candidates = {}
    for observed in observations:
        ident = observed["id"]
        # Keep per-article provenance, while using the current queue decision.
        current = queue.get(ident, {})
        row_record = {**observed, **{k: current[k] for k in ("status", "human_review", "last_observed") if k in current}}
        rows.append(review_row(row_record, duplicate=ident in encountered, now=now))
        encountered.add(ident)
        if observed.get("campaign_type") != "NEWS_ONLY":
            candidates[ident] = queue.get(ident, observed)
    failures = [{"source_url": h.url, "source_id": h.source_id, "reasons": [reason("SOURCE_UNREACHABLE", h.error or "Source unavailable")]} for h in source_health if not h.reachable]
    stats = {
        "articles_received": sum(h.entries_received for h in source_health),
        "articles_scanned": sum(h.entries_analyzed for h in source_health),
        "news_rejected": sum(q.get("campaign_type") == "NEWS_ONLY" for q in observations),
        "quest_candidates": len(candidates),
        "officially_verified": sum(q.get("evidence_verified") is True for q in candidates.values()),
        "zero_capital_pass": sum(q.get("cost_status") == "PASS" for q in candidates.values()),
        "actionable_quests": sum(q.get("qualification", {}).get("status") == "ACTIONABLE" and q.get("status") in ("READY", "NEEDS_APPROVAL") for q in candidates.values()),
        "awaiting_approval": sum(q.get("status") == "NEEDS_APPROVAL" or (q.get("human_review") or {}).get("decision") == "defer" for q in candidates.values()),
        "completed_tasks": sum(t.get("completion") is not None for q in queue.values() for t in q.get("tasks", {}).values()),
        "confirmed_rewards": len(rewards.get("actual_rewards_received", [])),
        "duplicate_observations": sum(any(r["code"] == "DUPLICATE" for r in row["reasons"]) for row in rows),
        "sources_unreachable": len(failures),
    }
    from qualification import qualify
    stats["qualification_counts"] = {status: sum(qualify(q, now)["status"] == status for q in candidates.values())
                                      for status in ("ACTIONABLE", "REVIEW_REQUIRED", "BLOCKED", "EXPIRED")}
    return {"statistics": stats, "candidates": rows, "source_failures": failures,
            "scope": "Articles are feed entries actually examined before the report cap; candidates/verified/PASS/actionable are distinct non-NEWS_ONLY canonical IDs observed this scan. Completed tasks and confirmed reward receipts are cumulative."}
