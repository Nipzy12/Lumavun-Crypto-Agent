"""Fail-closed zero-capital claim execution via independently vetted, authorized APIs.

Only API-based, gasless claims with an explicit official automation permission can
execute. No wallets, signatures, keys, browser automation or CAPTCHA bypass.
A provider submission is NEVER a verified received reward.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import re

from operations import operation_revision
from qualification import qualify
from triage import classify, revision

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "claim_adapters.json"
API_TIMEOUT = 10
MAX_RESPONSE = 65536
SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,48}$")
CAMPAIGN_ID = re.compile(r"^[a-f0-9]{20,64}$")
ALLOWED_REWARD = frozenset(("CONFIRMED_TOKEN_REWARD", "CONDITIONAL_TOKEN_REWARD"))
REQUIRED = {
    "id", "campaign_id", "official_url", "api_origin", "eligibility_path",
    "claim_path", "docs_url", "terms_url", "checked_at",
    "automation_allowed", "gasless_verified", "no_mandatory_fees_verified",
    "requires_token", "network",
}


def utcnow():
    return datetime.now(timezone.utc)


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


def _url(value):
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and u.hostname and "." in u.hostname
                and not u.username and not u.password and not u.query
                and not u.fragment and u.port in (None, 443)
                and not u.hostname.endswith((".local", ".internal", ".localhost"))
                and not re.fullmatch(r"[0-9.:]+", u.hostname))
    except (TypeError, ValueError):
        return False


def _path(value):
    return (isinstance(value, str) and value.startswith("/") and not value.startswith("//")
            and re.fullmatch(r"/[a-zA-Z0-9/_-]{1,150}", value) is not None
            and ".." not in value)


def validate_adapter(item, now=None):
    """No dynamic URL or arbitrary endpoints can be supplied via workflow inputs."""
    now = now or utcnow()
    if not isinstance(item, dict) or set(item) != REQUIRED:
        raise ValueError("Claim adapter must have exactly the reviewed schema")
    if not SAFE_ID.fullmatch(item["id"]) or not CAMPAIGN_ID.fullmatch(item["campaign_id"]):
        raise ValueError("Invalid adapter identity")
    if not all(_url(item[k]) for k in ("official_url", "api_origin", "docs_url", "terms_url")):
        raise ValueError("Invalid adapter official/API URL")
    api = urlsplit(item["api_origin"])
    if api.path not in ("", "/") or not all(_path(item[k]) for k in ("eligibility_path", "claim_path")):
        raise ValueError("Fixed API origin and explicit paths required")
    if item["eligibility_path"] == item["claim_path"]:
        raise ValueError("Entitlement and claim must be separate audited endpoints")
    if urlsplit(item["official_url"]).hostname != urlsplit(item["terms_url"]).hostname:
        raise ValueError("Campaign terms must be on official host")
    if urlsplit(item["docs_url"]).hostname not in (urlsplit(item["official_url"]).hostname, api.hostname):
        raise ValueError("API documentation must be linked to official or API host")
    if (item["automation_allowed"], item["gasless_verified"],
            item["no_mandatory_fees_verified"]) != (True, True, True):
        raise ValueError("No reviewed official automation/cost permission")
    if item["network"] != "solana-mainnet":
        raise ValueError("Only independently verified Solana mainnet claims are supported")
    if type(item["requires_token"]) is not bool:
        raise ValueError("Explicit provider authentication requirement needed")
    checked = _time(item["checked_at"])
    if checked is None or now < checked or now-checked > timedelta(days=30):
        raise ValueError("Official claim API permission/review is stale")
    return item


def adapters(path=REGISTRY, now=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema_version", "adapters"} or data["schema_version"] != 1 or not isinstance(data["adapters"], list):
        raise ValueError("Claim registry invalid")
    by_campaign = {}
    for item in data["adapters"]:
        validate_adapter(item, now)
        if item["campaign_id"] in by_campaign:
            raise ValueError("Duplicate claim adapter")
        by_campaign[item["campaign_id"]] = item
    if sum(bool(a["requires_token"]) for a in by_campaign.values()) > 1:
        raise ValueError("Only one credentialed provider per isolated secret is allowed")
    return by_campaign


def fingerprint(adapter):
    return sha256(json.dumps(adapter, sort_keys=True).encode()).hexdigest()


def gate(record, adapter, *, wallet, now=None):
    """Every failure blocks authorization and submission, regardless of score."""
    now = now or utcnow()
    reasons = []
    q = qualify(record, now)
    if q["status"] != "ACTIONABLE":
        reasons.append("NOT_ACTIONABLE")
    if q["reward_class"] not in ALLOWED_REWARD:
        reasons.append("NO_VERIFIED_TOKEN_REWARD")
    fields = q["fields"]
    if any(fields[k]["status"] != "PASS" for k in
           ("mandatory_fees", "gas_costs", "geographic_eligibility", "account_eligibility", "official_url")):
        reasons.append("COST_OR_ELIGIBILITY_UNVERIFIED")
    axes = classify(record, q)
    if axes["participation_cost"] != "FREE_VERIFIED" or axes["participation_model"] != "individual quest":
        reasons.append("NOT_ZERO_CAPITAL_INDIVIDUAL_QUEST")
    if record.get("id") != adapter["campaign_id"] or record.get("official_url") != adapter["official_url"]:
        reasons.append("OFFICIAL_CAMPAIGN_BINDING_MISMATCH")
    from wallet import validate_solana_address
    if not validate_solana_address(wallet):
        reasons.append("PUBLIC_WALLET_REQUIRED")
    if "Solana" not in (record.get("networks") or []):
        reasons.append("SOLANA_MAINNET_CAMPAIGN_EVIDENCE_REQUIRED")
    operations = record.get("operations") or {}
    if operations.get("state") not in ("SUBMITTED", "AWAITING_RESULT"):
        reasons.append("HUMAN_TASK_SUBMISSION_REQUIRED")
    tasks = record.get("tasks") or {}
    if not tasks or any(
        not isinstance(t, dict) or not t.get("completion")
        or (t.get("approval_reasons") and not t.get("approval"))
        for t in tasks.values()
    ):
        reasons.append("TASK_PROOFS_INCOMPLETE")
    if record.get("terms_review_required"):
        reasons.append("CHANGED_TERMS_UNREVIEWED")
    return reasons


def _events(record):
    return record.setdefault("operator_history", [])


def _latest_authorization(record):
    events = [e for e in _events(record) if e.get("kind") in ("CLAIM_AUTHORIZED", "CLAIM_REVOKED")]
    return sorted(events, key=lambda e: (e.get("at", ""), e.get("id", "")))[-1] if events else None


def current_approval(record, adapter, *, wallet, now=None):
    now = now or utcnow()
    event = _latest_authorization(record)
    if not event or event.get("kind") != "CLAIM_AUTHORIZED":
        return False
    expiry = _time(event.get("expires_at"))
    return (expiry is not None and now < expiry
            and event.get("fingerprint") == fingerprint(adapter)
            and event.get("revision") == revision(record)
            and event.get("wallet_commitment") == sha256(wallet.encode()).hexdigest())


def authorize(record, adapter, *, wallet, actor, expected_revision, request_id, now=None):
    now = now or utcnow()
    validate_adapter(adapter, now)
    if not isinstance(actor, str) or not actor.strip() or not isinstance(request_id, str) or not SAFE_ID.fullmatch(request_id):
        raise ValueError("Authenticated operator and unique request ID required")
    if expected_revision != revision(record):
        raise ValueError("Stale campaign revision; refresh live evidence")
    existing = next((e for e in _events(record) if e["id"] == request_id), None)
    if existing:
        raise ValueError("Request ID already used")
    reasons = gate(record, adapter, wallet=wallet, now=now)
    if reasons:
        raise ValueError("Claim authorization refused: " + ", ".join(reasons))
    event = {"id": request_id, "kind": "CLAIM_AUTHORIZED", "at": now.isoformat(),
             "expires_at": (now+timedelta(hours=6)).isoformat(), "actor": actor,
             "fingerprint": fingerprint(adapter), "revision": revision(record),
             "wallet_commitment": sha256(wallet.encode()).hexdigest()}
    _events(record).append(event)
    return event


def revoke(record, *, actor, request_id, now=None):
    now = now or utcnow()
    if not actor or not isinstance(request_id, str) or not SAFE_ID.fullmatch(request_id):
        raise ValueError("Authenticated operator required")
    if any(e.get("id") == request_id for e in _events(record)):
        raise ValueError("Request ID already used")
    event = {"id": request_id, "kind": "CLAIM_REVOKED", "at": now.isoformat(), "actor": actor}
    _events(record).append(event)
    return event


def already_dispatched(record):
    return any(e.get("kind") == "CLAIM_DISPATCHED" for e in _events(record))


def eligible_to_dispatch(record, adapter, wallet, now=None):
    reasons = gate(record, adapter, wallet=wallet, now=now)
    if not current_approval(record, adapter, wallet=wallet, now=now):
        reasons.append("NO_CURRENT_CAMPAIGN_APPROVAL")
    if already_dispatched(record):
        reasons.append("ALREADY_DISPATCHED_REVIEW_REQUIRED")
    return reasons


def _json_request(method, url, *, session, headers, params=None, payload=None):
    from source_security import check_source_destination
    if not _url(url) or not check_source_destination(url):
        raise ValueError("Official API destination not public/approved")
    response = session.request(method, url, params=params, json=payload,
                               headers=headers, allow_redirects=False, timeout=API_TIMEOUT,
                               stream=True)
    try:
        if response.status_code != 200:
            raise ValueError("Official API did not return HTTP 200")
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=8192):
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ValueError("Official API response size exceeded")
            chunks.append(chunk)
        obj = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("Official API response must be object")
        return obj
    finally:
        response.close()


def _headers(adapter, token):
    if adapter["requires_token"] and (not isinstance(token, str) or not token):
        raise ValueError("Missing provider-scoped claim API credential")
    return {"Accept": "application/json", "Content-Type": "application/json",
            **({"Authorization": "Bearer " + token} if adapter["requires_token"] else {})}


def verify_entitlement(adapter, record, *, wallet, session, token=""):
    """Mandatory live provider verification before any submission."""
    headers = _headers(adapter, token)
    obj = _json_request("GET", adapter["api_origin"]+adapter["eligibility_path"],
                        session=session, headers=headers,
                        params={"campaign_id":record["id"], "wallet":wallet})
    if (obj.get("campaign_id") != record["id"] or obj.get("wallet") != wallet
            or not all(obj.get(k) is True for k in ("eligible", "claimable", "active"))
            or obj.get("fee_required") is not False or obj.get("gas_required") is not False):
        raise ValueError("Live provider entitlement, free claim or campaign status not confirmed")
    return True


def submit_claim(adapter, record, *, wallet, request_id, session, token=""):
    """One HTTPS claim request. Caller MUST durably persist dispatch first."""
    obj = _json_request("POST", adapter["api_origin"]+adapter["claim_path"], session=session,
                        headers={**_headers(adapter, token), "Idempotency-Key": request_id},
                        payload={"campaign_id": record["id"], "wallet": wallet,
                                 "idempotency_key": request_id})
    if (obj.get("campaign_id") != record["id"] or obj.get("wallet") != wallet
            or not isinstance(obj.get("claim_id"), str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{4,120}", obj["claim_id"])
            or obj.get("status") not in ("accepted", "pending", "already_claimed")):
        raise ValueError("Unverifiable claim API acknowledgment")
    return {"status": "CLAIM_SUBMITTED_UNVERIFIED", "claim_id": obj["claim_id"],
            "provider_status": obj["status"],
            "note": "Provider acknowledgment only; not a received wallet reward"}


def claim_report(state, registry, *, wallet="", now=None):
    now = now or utcnow()
    rows = []
    for ident, record in sorted((state.get("quest_queue") or {}).items()):
        adapter = registry.get(ident)
        reasons = (gate(record, adapter, wallet=wallet, now=now)
                   if adapter else ["NO_REVIEWED_CLAIM_API_ADAPTER"])
        if adapter and not reasons:
            reasons += eligible_to_dispatch(record, adapter, wallet, now)
        rows.append({"campaign_id": ident, "status": "READY_TO_DISPATCH" if not reasons else "NOT_READY",
                     "blockers": sorted(set(reasons)),
                     "adapter": adapter["id"] if adapter else None,
                     "attempted": already_dispatched(record)})
    receipts = {
        (e.get("signature"), e.get("asset"))
        for store in (state.get("reward_wallets") or {}).values()
        for e in (store.get("events") or {}).values()
        if isinstance(e, dict) and e.get("attribution") == "CONFIRMED_QUEST_REWARD"
        and e.get("signature") and e.get("asset")
    }
    return {"schema_version": 1, "generated_at": now.isoformat(),
            "ready": sum(r["status"] == "READY_TO_DISPATCH" for r in rows),
            "attempted": sum(r["attempted"] for r in rows),
            "confirmed_wallet_receipts_existing_tracker": len(receipts),
            "receipt_authority": "finalized wallet attribution only; API acknowledgment is not income",
            "claims": rows}
