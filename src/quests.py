"""Quest intelligence and a conservative, non-executing task state machine."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, time, timezone
from typing import Any

from analysis import extract_eligibility, extract_tasks, has_reward_program

STATES = {"DISCOVERED", "VERIFIED", "READY", "NEEDS_APPROVAL", "COMPLETED", "REJECTED", "EXPIRED"}
APPROVAL_ACTIONS = {
    "wallet": r"\b(?:connect|sign|signature|approve|transaction|bridge|swap|mint|stake)\b",
    "account": r"\b(?:authorize|oauth|login|log in|sign up|register|account authorization)\b",
    "social": r"\b(?:follow|join|retweet|tweet|post|like|share|discord|telegram|vote)\b",
    "personal_information": r"\b(?:submit|email|phone|kyc|identity|address|upload|verify yourself)\b",
    "claim": r"\b(?:claim|redeem|collect reward)\b",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any, *, end: bool = False) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        if isinstance(value, (float, int)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if len(str(value)) == 10:
            day = datetime.fromisoformat(str(value)).date()
            return datetime.combine(day, time.max if end else time.min, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def task_policy(text: str) -> list[str]:
    reasons = [kind for kind, pattern in APPROVAL_ACTIONS.items() if re.search(pattern, text, re.I)]
    # Unknown task types have no implicit execution permission.
    if not reasons and not re.match(r"^(?:read|review|monitor|check public|view public|learn)\b", text, re.I):
        reasons.append("unclassified_action")
    return reasons


def task_checklist(tasks: list[str], quest_id: str) -> dict:
    result = {}
    for sentence in tasks:
        for text in re.split(r"[;\n]|(?:,\s*|\band\s+)(?=(?:follow|join|submit|connect|claim|register|read|review|verify|complete)\b)", sentence, flags=re.I):
            text = re.sub(r"\s+", " ", text).strip(" .-")
            if not text:
                continue
            ident = hashlib.sha256((quest_id + "\0" + text.casefold()).encode()).hexdigest()[:24]
            reasons = task_policy(text)
            result[ident] = {"id": ident, "text": text, "approval_reasons": reasons,
                             "status": "DISCOVERED", "approval": None, "completion": None}
    return result


def from_campaign(campaign, text: str = "", metadata: dict | None = None) -> dict:
    """Extract quoted fields; missing eligibility, dates or guarantees stay unknown."""
    metadata = metadata or {}
    text = text or campaign.raw_summary
    start = re.search(r"\b(?:starts?|begins?|opens?) (?:on )?(20\d{2}-\d{2}-\d{2})\b", text, re.I)
    networks = [name for name in ("Solana", "Ethereum", "Arbitrum", "Base", "Polygon", "Optimism", "Avalanche")
                if re.search(r"\b" + name + r"\b", text, re.I)]
    wallet_requirements = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                           if re.search(r"\bwallet\b", s, re.I)]
    eligibility = extract_eligibility(text)
    open_eligibility = bool(re.search(r"\b(?:everyone is eligible|open to everyone|no eligibility restrictions|all users are eligible)\b", text, re.I))
    if re.search(r"\b(?:except|excluding|not eligible|residents|citizens|over \d+|age|whitelist|allowlist|wallet must)\b", text, re.I):
        open_eligibility = False
    reward_type = ("OFFCHAIN_POINTS" if campaign.reward_status == "points_only" or campaign.opportunity_type == "POINTS_PROGRAM"
                   else "NFT" if re.search(r"\bNFT\b", text, re.I)
                   else "TOKEN" if re.search(r"\btokens?\b", text, re.I) else "UNSPECIFIED")
    guaranteed = None
    if re.search(r"\b(?:not guaranteed|no guaranteed|raffle|lottery|chance to win)\b", text, re.I):
        guaranteed = False
    elif re.search(r"\b(?:guaranteed token rewards?|token rewards? (?:are|is) guaranteed)\b", text, re.I):
        guaranteed = True
    record = {
        "id": campaign.campaign_id, "project": campaign.project, "name": campaign.title,
        "official_url": campaign.official_url, "source": campaign.source,
        "announcement_url": campaign.announcement_url or campaign.url,
        "evidence_checks": {"official": bool(campaign.official_url and campaign.source_verification_status in ("official_feed", "authorized_galxe_api")),
                            "reward": bool(campaign.evidence_verified or has_reward_program(campaign.title + " " + text)),
                            "participation": bool(campaign.tasks), "organizer": bool(campaign.project)},
        "evidence_verified": campaign.evidence_verified,
        "evidence": [campaign.opportunity_reason, campaign.reward_text or "", *campaign.tasks],
        "source_verification_status": campaign.source_verification_status,
        "start_at": start.group(1) if start else None, "end_at": campaign.deadline,
        "tasks": task_checklist(extract_tasks(text, limit=100) or campaign.tasks, campaign.campaign_id),
        "reward_type": reward_type, "reward_status": campaign.reward_status,
        "reward_guaranteed": guaranteed, "reward_evidence": campaign.reward_text,
        "networks": networks, "wallet_requirements": wallet_requirements,
        "mandatory_fees": campaign.zero_capital_blockers, "cost_status": campaign.cost_status,
        "cost_evidence": campaign.zero_capital_reason, "eligibility_restrictions": eligibility,
        "eligibility_text": text[:6000],
        "eligibility_status": "PASS" if open_eligibility else "UNKNOWN",
        "eligibility_evidence": "Explicit unrestricted eligibility" if open_eligibility else "Not checked",
        "risk_indicators": campaign.risk_flags, "risk_explanation": campaign.risk_explanation,
        "risk_band": campaign.risk_band, "campaign_type": campaign.opportunity_type,
        "platform_status": "", "status": "DISCOVERED",
    }
    for key in ("start_at", "end_at", "networks", "eligibility_status", "eligibility_evidence", "platform_status"):
        if key in metadata:
            record[key] = metadata[key]
    return record


def _status(record: dict, now: datetime) -> str:
    end = parse_time(record.get("end_at"), end=True)
    if end and end < now or record.get("platform_status") == "Expired":
        return "EXPIRED"
    if (record.get("campaign_type") == "NEWS_ONLY" or record.get("cost_status") == "FAIL"
            or record.get("eligibility_status") == "FAIL" or record.get("risk_band") == "high"
            or record.get("platform_status") in ("Deleted", "CapReached", "Draft")):
        return "REJECTED"
    if (record.get("human_review") or {}).get("decision") == "reject":
        return "REJECTED"
    if not record.get("evidence_verified"):
        return "DISCOVERED"
    if (record.get("human_review") or {}).get("decision") == "defer":
        return "VERIFIED"
    observed = parse_time(record.get("last_observed"))
    if observed is None or (now - observed).total_seconds() > 86400:
        return "VERIFIED"
    start = parse_time(record.get("start_at"))
    if (record.get("cost_status") != "PASS" or record.get("eligibility_status") != "PASS"
            or not record.get("tasks") or (start and start > now)
            or record.get("platform_status") == "NotStarted"
            or (record.get("end_at") and end is None)
            or (record.get("start_at") and start is None)):
        return "VERIFIED"
    tasks = list(record["tasks"].values())
    if all(t.get("completion") for t in tasks):
        return "COMPLETED"
    if any(t["approval_reasons"] and not t.get("approval") and not t.get("completion") for t in tasks):
        return "NEEDS_APPROVAL"
    return "READY"


def refresh_status(record: dict, now: datetime) -> None:
    from qualification import qualify, migrate_record
    migrate_record(record)
    record["qualification"] = qualify(record, now)
    record["status"] = _status(record, now)
    from diagnostics import candidate_reasons
    record["reasons"] = candidate_reasons(record, now=now)
    for task in record.get("tasks", {}).values():
        if task.get("completion"):
            task["status"] = "COMPLETED"
        elif record["status"] in ("DISCOVERED", "VERIFIED", "REJECTED", "EXPIRED"):
            task["status"] = record["status"]
        elif task["approval_reasons"] and not task.get("approval"):
            task["status"] = "NEEDS_APPROVAL"
        else:
            task["status"] = "READY"


def update_queue(state: dict, quests: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    queue = state.setdefault("quest_queue", {})
    for incoming in quests:
        # Keep ordinary news out of the persistent task queue; reports count it.
        if incoming["campaign_type"] == "NEWS_ONLY":
            continue
        old = queue.get(incoming["id"], {})
        from qualification import history_union
        incoming["review_history"] = history_union(old.get("review_history"), incoming.get("review_history"))
        incoming["task_archive"] = {**(old.get("task_archive") or {}), **{k:v for k,v in old.get("tasks", {}).items() if k not in incoming["tasks"]}}
        incoming["first_seen"] = old.get("first_seen", now.isoformat())
        incoming["verified_at"] = old.get("verified_at") or (now.isoformat() if incoming["evidence_verified"] else None)
        incoming["updated_at"] = now.isoformat()
        incoming["last_observed"] = now.isoformat()
        # Consent applies to the exact quest terms, not just an unchanged verb.
        terms = repr([incoming.get(k) for k in ("official_url", "cost_status", "eligibility_restrictions", "end_at", "risk_indicators")])
        incoming["terms_hash"] = hashlib.sha256(terms.encode()).hexdigest()
        if old.get("terms_hash") == incoming["terms_hash"]:
            incoming["human_review"] = old.get("human_review")
        for ident, task in incoming["tasks"].items():
            previous = old.get("tasks", {}).get(ident, {})
            if old.get("terms_hash") == incoming["terms_hash"]:
                task["approval"] = previous.get("approval")
            task["completion"] = previous.get("completion")
        queue[incoming["id"]] = incoming
    for record in queue.values():
        before = record.get("status")
        refresh_status(record, now)
        if before != record["status"]:
            record["updated_at"] = now.isoformat()
    return queue


def record_task_action(state: dict, quest_id: str, task_id: str, action: str, evidence: str,
                       now: datetime | None = None) -> None:
    """Record a human decision, never execute a wallet, social or account action."""
    now = now or datetime.now(timezone.utc)
    record = state["quest_queue"][quest_id]
    refresh_status(record, now)
    task = record["tasks"][task_id]
    if record["status"] not in ("READY", "NEEDS_APPROVAL") or record["qualification"]["status"] != "ACTIONABLE":
        raise ValueError("Quest is not eligible for task action")
    if not evidence.strip() or re.fullmatch(r"https?://\S+", evidence.strip()):
        raise ValueError("A URL alone is not evidence of approval or completion")
    if task.get("completion"):
        raise ValueError("Task already completed")
    if action == "approve":
        task["approval"] = {"at": now.isoformat(), "evidence": evidence[:500], "kind": "human_approval"}
    elif action == "complete":
        if task["approval_reasons"] and not task.get("approval"):
            raise ValueError("Human approval required before completion")
        task["completion"] = {"at": now.isoformat(), "evidence": evidence[:500], "kind": "human_confirmation"}
    else:
        raise ValueError("Unsupported action")
    record["updated_at"] = now.isoformat()
    refresh_status(record, now)


def queue_digest(queue: dict, observed_ids: set[str] | None = None) -> dict:
    records = list(queue.values())
    return {
        "new_verified_quests": sum(str(q.get("verified_at") or "").startswith(now_iso()[:10]) for q in records),
        "verified_quests": sum(q["evidence_verified"] for q in records if observed_ids is None or q["id"] in observed_ids),
        "pending_approvals": sum(t["status"] == "NEEDS_APPROVAL" for q in records for t in q["tasks"].values()),
        "completed_tasks": sum(t["status"] == "COMPLETED" for q in records for t in q["tasks"].values()),
        "rejected_campaigns": sum(q["status"] == "REJECTED" for q in records),
        "expired_quests": sum(q["status"] == "EXPIRED" for q in records),
        "actionable_quests": sum(q["status"] in ("READY", "NEEDS_APPROVAL") for q in records if observed_ids is None or q["id"] in observed_ids),
    }


def review_candidate(state: dict, quest_id: str, decision: str, evidence: str,
                     now: datetime | None = None, *, reason: str = "", reviewer: str = "operator") -> None:
    """Explicit candidate review never supplies missing facts or task consent."""
    from copy import deepcopy
    now = now or datetime.now(timezone.utc)
    if decision not in ("accept", "reject", "defer"):
        raise ValueError("Review decision must be accept, reject or defer")
    if not evidence.strip() or re.fullmatch(r"https?://\S+", evidence.strip()):
        raise ValueError("A substantive human review note is required")
    record = state["quest_queue"][quest_id]
    trial = deepcopy(record)
    trial.pop("human_review", None)
    refresh_status(trial, now)
    if decision == "accept" and (trial["status"] not in ("READY", "NEEDS_APPROVAL", "COMPLETED") or trial["qualification"]["status"] != "ACTIONABLE"):
        raise ValueError("Cannot accept: evidence, PASS costs, eligibility and freshness must already pass; UNKNOWN is never approved")
    from qualification import qualify, history_union
    previous_status = qualify(record, now)["status"]
    record.setdefault("review_history", [])
    record["human_review"] = {"kind": "human_review", "decision": decision, "at": now.isoformat(), "evidence": evidence[:500]}
    record["updated_at"] = now.isoformat()
    refresh_status(record, now)

    import uuid
    entry = {"id": uuid.uuid4().hex, "decision": decision, "evidence": evidence[:500],
             "reviewer": reviewer[:100], "reason": (reason or evidence)[:500], "at": now.isoformat(),
             "previous_status": previous_status, "new_status": record["qualification"]["status"]}
    record["review_history"] = history_union(record.get("review_history"), [entry])
