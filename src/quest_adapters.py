"""Documented, read-only quest adapters. No private APIs or HTML scraping."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml

from analysis import evaluate_zero_capital, extract_tasks, extract_reward, score_risk
from discovery import Campaign, scan_sources
from quests import from_campaign
from sources import SourceSpec, validate_source_url, sanitize_url, _respect_rate_limit

GALXE_ENDPOINT = "https://graphigo-business.prd.galaxy.eco/query"
GALXE_DOCS = "https://docs.galxe.com/galxe-integration/api-reference/quest"
QUEST_QUERY = """query GetQuest($id: ID!) {
  quest(id: $id) {
    id name type status description startTime endTime cap participantsCount
    loyaltyPoints gasType space { id name } chain
  }
}"""
ELIGIBILITY_QUERY = """query CheckQuestEligibility($questId: ID!, $address: String!) {
  quest(id: $questId) {
    id name status credentialGroups(address: $address) {
      id name conditionRelation conditions { expression eligible }
      rewards { expression eligible rewardType rewardCount }
    }
  }
}"""


def load_quest_config(path: Path | None = None) -> dict:
    path = path or Path(__file__).resolve().parent.parent / "config" / "quest_sources.yaml"
    try:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Quest configuration must be a mapping")
        if not isinstance(data.get("galxe", {}), dict):
            raise ValueError("Invalid Galxe configuration")
        for key in ("public_feeds", "manual_campaigns", "other_platforms"):
            if not isinstance(data.get(key, []), list):
                raise ValueError("Invalid adapter list")
        if not isinstance(data.get("reward_tracking", {}), dict):
            raise ValueError("Invalid tracker configuration")
        return data
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"configuration_error": type(exc).__name__}


def status(adapter: str, state: str, reason: str, **extra) -> dict:
    return {"adapter": adapter, "status": state, "reason": reason, **extra}


def _query(query: str, variables: dict, token: str, session=requests) -> tuple[dict | None, dict | None]:
    try:
        _respect_rate_limit(urlsplit(GALXE_ENDPOINT).hostname, 1.0)
        response = session.post(GALXE_ENDPOINT, json={"query": query, "variables": variables},
                                headers={"access-token": token}, timeout=20, allow_redirects=False)
        if response.status_code in (401, 403):
            return None, status("galxe", "AUTH_REQUIRED", "Authorized Galxe API token required")
        if response.status_code == 429:
            return None, status("galxe", "RATE_LIMITED", "Deferred until next scheduled scan")
        response.raise_for_status()
        if response.status_code != 200:
            return None, status("galxe", "UNSUPPORTED", "Unexpected API HTTP status")
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Expected GraphQL object")
        if body.get("errors"):
            codes = {str(e.get("extensions", {}).get("category", "")) for e in body["errors"] if isinstance(e, dict)}
            state = "AUTH_REQUIRED" if codes & {"AUTHENTICATION", "AUTHORIZATION"} else "RATE_LIMITED" if "RATE_LIMIT" in codes else "UNSUPPORTED"
            return None, status("galxe", state, "API rejected query; no partial result accepted")
        data = body.get("data")
        quest = data.get("quest") if isinstance(data, dict) else None
        if not isinstance(quest, dict):
            raise ValueError("Missing quest object")
        return quest, None
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # Never include response bodies, URLs with tokens or exception messages.
        return None, status("galxe", "UNAVAILABLE", "API transport or schema validation failed")


def eligibility_from_groups(groups) -> str:
    if not isinstance(groups, list) or not groups:
        return "UNKNOWN"
    outcomes = []
    for group in groups:
        if not isinstance(group, dict):
            return "UNKNOWN"
        conditions = group.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            return "UNKNOWN"
        values = [c.get("eligible") if isinstance(c, dict) else None for c in conditions]
        if any(type(v) is not bool for v in values):
            return "UNKNOWN"
        relation = group.get("conditionRelation")
        if relation == "ALL":
            outcomes.append(all(values))
        elif relation == "ANY":
            outcomes.append(any(values))
        else:
            return "UNKNOWN"
    # Conservative across groups: no undocumented OR between groups.
    return "PASS" if all(outcomes) else "FAIL"


def galxe_discover(settings: dict, config: dict, session=requests) -> tuple[list[Campaign], list[dict]]:
    token = os.environ.get("GALXE_ACCESS_TOKEN", "")
    if not token:
        return [], [status("galxe", "AUTH_REQUIRED", "Documented access-token is absent; no API request made", documentation=GALXE_DOCS)]
    if settings.get("enabled") is not True:
        return [], [status("galxe", "DISABLED", "Operator has not enabled authorized API access")]
    entries = settings.get("quests")
    if not isinstance(entries, list) or not entries:
        return [], [status("galxe", "UNSUPPORTED", "Configure verified quest ID / official URL pairs; URLs are never invented")]
    campaigns, diagnostics = [], []
    for entry in entries[:10]:
        if not isinstance(entry, dict):
            diagnostics.append(status("galxe", "UNSUPPORTED", "Invalid quest entry"))
            continue
        ident, url = str(entry.get("id", "")), str(entry.get("official_url", ""))
        if (not ident or not validate_source_url(url) or urlsplit(url).hostname != "app.galxe.com"
                or urlsplit(url).username or entry.get("reviewed") is not True):
            diagnostics.append(status("galxe", "UNSUPPORTED", "Reviewed official Galxe URL and ID required"))
            continue
        data, failure = _query(QUEST_QUERY, {"id": ident}, token, session)
        if failure:
            diagnostics.append(failure)
            if failure["status"] in ("AUTH_REQUIRED", "RATE_LIMITED"):
                break
            continue
        if str(data.get("id")) != ident or not isinstance(data.get("description"), str) or not isinstance(data.get("space"), dict):
            diagnostics.append(status("galxe", "UNSUPPORTED", "Quest schema or identity mismatch"))
            continue
        if data.get("status") not in ("Active", "NotStarted", "Expired", "CapReached", "Deleted", "Draft"):
            diagnostics.append(status("galxe", "UNSUPPORTED", "Unknown quest status"))
            continue
        text = data["description"]
        tasks = extract_tasks(text)
        project = data["space"].get("name")
        reward_type = data.get("type")
        types = {"Airdrop": "CONFIRMED_AIRDROP", "Points": "POINTS_PROGRAM", "Drop": "FREE_QUEST", "MysteryBox": "FREE_QUEST"}
        evidence_ok = bool(project and data.get("name") and tasks and reward_type in types)
        zc = evaluate_zero_capital(data.get("name", ""), text, config)
        if data.get("gasType") == "Gas":
            zc.ok, zc.reason = False, "Galxe gasType=Gas: mandatory paid transaction"
        elif data.get("gasType") not in ("Gasless",):
            zc.ok = False if zc.ok is False else None
        # Gasless alone never establishes that every credential task is free.
        reward = extract_reward(text, config)
        flags = [str(rule["id"]) for rule in config.get("risk_rules", []) if any(str(p).lower() in text.lower() for p in rule.get("patterns", []))]
        risk = score_risk(flags, config)
        campaign = Campaign(title=str(data.get("name", "")), project=str(project or ""), url=sanitize_url(url), source=GALXE_ENDPOINT,
                            official_url=sanitize_url(url), announcement_url=sanitize_url(url), source_id="galxe:" + ident,
                            source_is_official=True, source_verification_status="authorized_galxe_api", raw_summary=text[:350],
                            tasks=tasks, opportunity_type=types.get(reward_type, "UNVERIFIED") if evidence_ok else "UNVERIFIED",
                            opportunity_reason="Documented authenticated quest metadata with organizer and task evidence",
                            evidence_verified=evidence_ok, zero_capital_ok=zc.ok, cost_status=zc.status,
                            zero_capital_reason=zc.reason, zero_capital_blockers=zc.blockers, risk_flags=flags,
                            risk_band=risk.band, risk_score=risk.score, risk_explanation=risk.explanation,
                            reward_status="points_only" if reward_type == "Points" else reward.status,
                            reward_text=reward.text, reward_note=reward.note)
        metadata = {"start_at": data.get("startTime"), "end_at": data.get("endTime"), "networks": [str(data["chain"])] if data.get("chain") else [],
                    "platform_status": data.get("status", ""), "eligibility_status": "UNKNOWN"}
        cap, count = data.get("cap"), data.get("participantsCount")
        if type(cap) is int and cap > 0 and type(count) is int and count >= cap:
            metadata["platform_status"] = "CapReached"
        address = str(settings.get("eligibility_address", "")).strip()
        if address and settings.get("eligibility_checks") is True:
            eligibility, failure = _query(ELIGIBILITY_QUERY, {"questId": ident, "address": address}, token, session)
            if failure:
                diagnostics.append(failure)
                if failure["status"] in ("AUTH_REQUIRED", "RATE_LIMITED"):
                    break
            elif str(eligibility.get("id")) == ident:
                metadata["eligibility_status"] = eligibility_from_groups(eligibility.get("credentialGroups"))
                metadata["eligibility_evidence"] = "Authorized credentialGroups query; all group conditions checked"
        campaign.quest_intelligence = from_campaign(campaign, text, metadata)
        campaign.quest_intelligence['gas_status'] = 'PASS' if data.get('gasType') == 'Gasless' else 'FAIL' if data.get('gasType') == 'Gas' else 'UNKNOWN'
        campaign.quest_intelligence['gas_evidence'] = 'Authorized API gasType=' + str(data.get('gasType'))
        campaigns.append(campaign)
        diagnostics.append(status("galxe", "OK", "Documented quest query verified at runtime", quest_id=ident))
    return campaigns, diagnostics


def discover_quests(campaigns: list[Campaign], source_health: list, config: dict, settings: dict | None = None) -> tuple[list[Campaign], list[dict]]:
    settings = settings if settings is not None else load_quest_config()
    results = list(campaigns)
    diagnostics = [status("official_feeds", "OK" if any(h.reachable for h in source_health) else "UNAVAILABLE",
                          "Existing public feeds retain HTTP, robots and rate-limit checks")]
    if settings.get("configuration_error"):
        diagnostics.append(status("configuration", "UNSUPPORTED", "Invalid quest configuration"))
        return results, diagnostics
    galxe, galxe_status = galxe_discover(settings.get("galxe", {}), config)
    results.extend(galxe)
    diagnostics.extend(galxe_status)
    for entry in settings.get("public_feeds", [])[:10]:
        if not isinstance(entry, dict) or entry.get("reviewed_public_access") is not True or not validate_source_url(str(entry.get("documentation_url", ""))):
            diagnostics.append(status("public_feed", "UNSUPPORTED", "Documented public access must be reviewed before enabling"))
            continue
        spec = SourceSpec(id=str(entry.get("id", "quest-feed")), url=str(entry.get("url", "")), official=True, category="official_project")
        found, health, errors = scan_sources([spec], config=config)
        results.extend(found)
        source_health.extend(health)
        diagnostics.append(status(spec.id, "OK" if health and health[0].reachable else "UNAVAILABLE", "Public feed verified" if not errors else "Public feed unavailable"))
    # A manually selected URL is verified against an observed official feed,
    # never by scraping a campaign page or trusting user-provided reward claims.
    for entry in settings.get("manual_campaigns", [])[:50]:
        if not isinstance(entry, dict):
            diagnostics.append(status("manual_url", "UNSUPPORTED", "Expected official_url and official_feed_id"))
            continue
        url = sanitize_url(str(entry.get("official_url", "")))
        matches = [c for c in results if c.source_id == entry.get("official_feed_id") and c.official_url == url and c.evidence_verified]
        diagnostics.append(status("manual_url", "OK" if matches else "UNSUPPORTED",
                                  "Official feed corroborates configured URL" if matches else "URL has no verified official-feed announcement; page scraping disabled", official_url=url))
    for name in settings.get("other_platforms", []):
        diagnostics.append(status(str(name), "UNSUPPORTED", "No reviewed documented adapter; use an authorized public feed"))
    return results, diagnostics
