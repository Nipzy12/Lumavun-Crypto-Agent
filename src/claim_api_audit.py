"""Evidence-based public API classification. Never activates a claim adapter by itself.

The source audit distinguishes official APIs, recipient claim APIs, signed claims,
testnet faucets, and issuer-only distribution. External documentation is reviewed
by a human; storing a URL does not independently establish permission.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import json
import re

RESEARCH_PATH = Path(__file__).resolve().parents[1] / "config" / "claim_api_research.json"
RESEARCH_KEYS = {"schema_version", "observed_at", "scope", "review_policy",
                 "candidates", "approved_adapters"}
CANDIDATE_KEYS = {
    "id", "name", "network", "api_role", "status", "reason_code",
    "document_url", "additional_evidence_urls", "notes", "claim_deadline",
    "automation_allowed", "gasless_for_participant", "mainnet_income",
    "official_claim_contract",
}
APPROVAL_KEYS = {
    "id", "campaign_id", "official_url", "api_origin", "docs_url",
    "automation_permission_evidence", "gas_and_fee_evidence",
    "participant_eligibility_evidence", "checked_at",
}
IDS = re.compile(r"^[a-z][a-z0-9-]{2,64}$")
CAMPAIGN = re.compile(r"^[a-f0-9]{20,64}$")
CATEGORIES = {"TESTNET_FAUCET", "ISSUER_DISTRIBUTION",
              "SIGNED_RECIPIENT_CLAIM", "REWARD_ORACLE_AND_SIGNED_ONCHAIN_CLAIM",
              "READ_ONLY_CLAIM_CHECKER", "SIGNED_TRANSACTION_BUILDER",
              "ORGANIZER_ELIGIBILITY_AND_MANUAL_CLAIM", "PARTICIPANT_CLAIM",
              "FEE_SPONSORSHIP_INFRASTRUCTURE", "ONCHAIN_DISTRIBUTION_PROTOCOL"}
STATUSES = {"EXCLUDED", "REVIEW_REQUIRED", "APPROVED"}


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _iso(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _official_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (parsed.scheme == "https" and host.count(".") >= 1
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment
                and not parsed.query and not re.fullmatch(r"[0-9.:]+", host)
                and not host.endswith((".local", ".internal", ".localhost")))
    except ValueError:
        return False


def load_research(path=RESEARCH_PATH, *, now=None):
    """Validate a strictly bounded audit file and cross-linked positive approvals."""
    now = now or datetime.now(timezone.utc)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != RESEARCH_KEYS or data["schema_version"] != 1:
        raise ValueError("Invalid research document schema")
    observed = _date(data["observed_at"])
    if not observed or observed > now.date() or not all(
        isinstance(data[k], str) and 10 <= len(data[k]) <= 1500
        for k in ("scope", "review_policy")
    ):
        raise ValueError("Invalid audit date or scope")
    if not isinstance(data["candidates"], list) or not isinstance(data["approved_adapters"], list):
        raise ValueError("Invalid candidate and approval lists")
    if len(data["candidates"]) > 100 or len(data["approved_adapters"]) > 50:
        raise ValueError("Research registry too large")
    by_id = {}
    for record in data["candidates"]:
        if not isinstance(record, dict) or set(record) != CANDIDATE_KEYS:
            raise ValueError("Invalid research candidate fields")
        ident = record["id"]
        if not isinstance(ident, str) or not IDS.fullmatch(ident) or ident in by_id:
            raise ValueError("Invalid or duplicate candidate ID")
        if (record["status"] not in STATUSES or record["api_role"] not in CATEGORIES
                or not _official_url(record["document_url"])
                or not isinstance(record["additional_evidence_urls"], list)
                or len(record["additional_evidence_urls"]) > 8
                or not all(_official_url(u) for u in record["additional_evidence_urls"])):
            raise ValueError("Untrusted candidate documentation or classification")
        if (not isinstance(record["name"], str) or not 3 <= len(record["name"]) <= 160
                or not isinstance(record["network"], str) or not record["network"]
                or not isinstance(record["notes"], str) or not 10 <= len(record["notes"]) <= 1000
                or not isinstance(record["reason_code"], str)
                or not re.fullmatch(r"[A-Z_]{4,80}", record["reason_code"])):
            raise ValueError("Incomplete candidate rationale")
        if record["claim_deadline"] is not None and _date(record["claim_deadline"]) is None:
            raise ValueError("Invalid claim deadline")
        for field in ("automation_allowed", "mainnet_income", "official_claim_contract"):
            if type(record[field]) is not bool:
                raise ValueError("Evidence flags must be explicit")
        if record["gasless_for_participant"] is not None and type(record["gasless_for_participant"]) is not bool:
            raise ValueError("Unknown gas must remain null")
        if record["status"] == "APPROVED":
            if not (record["api_role"] == "PARTICIPANT_CLAIM"
                    and record["network"] == "solana-mainnet"
                    and record["automation_allowed"]
                    and record["gasless_for_participant"] is True
                    and record["mainnet_income"]
                    and record["official_claim_contract"]
                    and (_date(record["claim_deadline"]) is not None and
                         _date(record["claim_deadline"]) >= now.date())):
                raise ValueError("Approved candidate does not satisfy zero-cost mainnet scope")
        by_id[ident] = record
    linked = {}
    for approval in data["approved_adapters"]:
        if not isinstance(approval, dict) or set(approval) != APPROVAL_KEYS:
            raise ValueError("Invalid approved adapter evidence")
        ident = approval["id"]
        if ident not in by_id or by_id[ident]["status"] != "APPROVED" or ident in linked:
            raise ValueError("Approved adapter must reference an independently reviewed positive candidate")
        if not isinstance(approval["campaign_id"], str) or not CAMPAIGN.fullmatch(approval["campaign_id"]):
            raise ValueError("Invalid canonical campaign ID")
        if any(not _official_url(approval[k]) for k in APPROVAL_KEYS - {"id", "campaign_id", "checked_at"}):
            raise ValueError("A documented official evidence URL is mandatory for each gate")
        verified = _iso(approval["checked_at"])
        if verified is None or not timedelta(0) <= now-verified <= timedelta(days=30):
            raise ValueError("Claim approval evidence is stale")
        if approval["official_url"] != by_id[ident]["document_url"] and \
           approval["official_url"] not in by_id[ident]["additional_evidence_urls"]:
            raise ValueError("Positive claim official URL must be reviewed in source audit")
        linked[ident] = approval
    return data


def approved_claim_adapters(path=RESEARCH_PATH, *, now=None):
    return {entry["campaign_id"]: entry for entry in load_research(path, now=now)["approved_adapters"]}


def audit_report(path=RESEARCH_PATH, *, now=None):
    now = now or datetime.now(timezone.utc)
    data = load_research(path, now=now)
    counts = Counter(row["status"] for row in data["candidates"])
    reason_counts = Counter(row["reason_code"] for row in data["candidates"]
                            if row["status"] != "APPROVED")
    notice = []
    for row in data["candidates"]:
        deadline = _date(row["claim_deadline"])
        if deadline is not None:
            notice.append({
                "source_id": row["id"],
                "claim_deadline": deadline.isoformat(),
                "days_until_deadline": (deadline-now.date()).days,
                "note": "Informational only; not claim eligibility or a proof of zero fees",
            })
    return {
        "schema_version": 1, "generated_at": now.isoformat(),
        "observed_at": data["observed_at"],
        "stale_review": (now.date()-_date(data["observed_at"])).days > 7,
        "reviewed_sources": len(data["candidates"]),
        "excluded": counts["EXCLUDED"], "review_required": counts["REVIEW_REQUIRED"],
        "approved_claim_apis": len(data["approved_adapters"]),
        "approved_campaigns": [a["campaign_id"] for a in data["approved_adapters"]],
        "rejection_reasons": dict(sorted(reason_counts.items())),
        "notices": notice,
        "candidates": [{
            "id": row["id"], "name": row["name"], "network": row["network"],
            "status": row["status"], "reason_code": row["reason_code"],
            "document_url": row["document_url"]
        } for row in data["candidates"]],
        "payouts_confirmed_from_this_audit": 0,
        "claim_api_authorization": "Never inferred from API availability; no active provider call was made",
    }
