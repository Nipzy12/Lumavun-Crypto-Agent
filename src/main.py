"""
LUMAVUN Crypto Agent V1.3 - entrypoint.

The orchestrator composes the source layer, the analysis engine, the
Phantom wallet monitor, Telegram reporting, persistent state and the report
writers.

Design rules that are enforced here:

* One failing integration does not stop the run. Telegram errors, dead
  sources and RPC failures are reported, not raised.
* A campaign is notified once. Notification history lives in the persisted
  state, which is restored from the previous workflow run.
* Nothing is signed, claimed, deposited or traded. Every report says so.
* An unhandled error is *not* caught here: it propagates, the process exits
  non-zero, and GitHub Actions fails. Silent success is the failure mode we
  refuse.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config_loader import effective_sources, load_config
from discovery import (
    Campaign,
    deduplicate_campaigns,
    scan_sources,
)
from reporter import build_report, write_json_report, write_markdown_report
from state import (
    load_state_from_artifact,
    mark_run,
    record_notification,
    save_state_locally,
    was_notified,
)
from telegram import send_messages, build_daily_summary, build_opportunity_block, chunk_message
from wallet import read_wallet
from quest_adapters import discover_quests, load_quest_config
from quests import from_campaign, update_queue, queue_digest
from rewards import update_rewards
from diagnostics import discovery_report

# Reports contain Indonesian text and status symbols. Force UTF-8 so a
# non-UTF-8 console (for example a Windows cp1252 shell) cannot crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(
    level=os.getenv("LUMAVUN_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lumavun")

AGENT_VERSION = "1.4.4"


def _env_list(name: str) -> List[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def campaign_payload(campaign: Campaign) -> Dict[str, Any]:
    """The subset of a campaign that notifications need."""
    return {
        "campaign_id": campaign.campaign_id,
        "title": campaign.title,
        "project": campaign.project,
        "url": campaign.url,
        "source": campaign.source,
        "announcement_url": campaign.announcement_url,
        "official_url": campaign.official_url,
        "opportunity_type": campaign.opportunity_type,
        "opportunity_reason": campaign.opportunity_reason,
        "deadline": campaign.deadline,
        "deadline_text": campaign.deadline_text,
        "tasks": list(campaign.tasks),
        "reward_status": campaign.reward_status,
        "reward_text": campaign.reward_text,
        "reward_note": campaign.reward_note,
        "zero_capital_ok": campaign.zero_capital_ok,
        "cost_status": campaign.cost_status,
        "evidence_verified": campaign.evidence_verified,
        "source_verification_status": campaign.source_verification_status,
        "zero_capital_reason": campaign.zero_capital_reason,
        "risk_flags": list(campaign.risk_flags),
        "risk_score": campaign.risk_score,
        "risk_band": campaign.risk_band,
        "risk_explanation": list(campaign.risk_explanation),
        "classification": campaign.classification,
        "cross_checked": campaign.cross_checked,
        "cross_check_note": campaign.cross_check_note,
    }


def main() -> int:
    logger.info("LUMAVUN Crypto Agent V%s starting", AGENT_VERSION)
    scan_time = datetime.now(timezone.utc).isoformat()
    config = load_config()

    specs = effective_sources()
    logger.info(
        "Sources configured: %d (%s)",
        len(specs),
        ", ".join(sorted({spec.category for spec in specs})) or "none",
    )
    if not specs:
        logger.warning(
            "No sources configured. Set AIRDROP_RSS_FEEDS or enable sources in "
            "config/vetted_sources.yaml."
        )

    campaigns, source_health, errors = scan_sources(specs, config=config)
    logger.info(
        "Scan finished: %d campaigns from %d sources (%d errors)",
        len(campaigns),
        len(source_health),
        len(errors),
    )

    quest_settings = load_quest_config()
    campaigns, adapter_health = discover_quests(campaigns, source_health, config, quest_settings)
    state = load_state_from_artifact()
    previous_ids = set(state.get("seen", {})) | set(state.get("quest_queue", {}))
    observed_quests = [q for health in source_health for q in health.observations]
    observed_keys = {(q["id"], q["source"]) for q in observed_quests}
    for campaign in campaigns:
        record = campaign.quest_intelligence or from_campaign(campaign)
        if (record["id"], record["source"]) not in observed_keys:
            observed_quests.append(record)
            observed_keys.add((record["id"], record["source"]))
    unique_quests = {}
    for record in observed_quests:
        previous = unique_quests.get(record["id"])
        if previous is None or record["evidence_verified"] or not previous["evidence_verified"]:
            unique_quests[record["id"]] = record
    from qualification import qualify
    prior_qualifications = {k:qualify(v) for k,v in state.get("quest_queue", {}).items()}
    queue = update_queue(state, list(unique_quests.values()))
    from recovery import revalidate, recovery_report
    revalidate(state, specs, config, observations=list(unique_quests.values()), prior_qualifications=prior_qualifications)
    quest_summary = queue_digest(queue, {q["id"] for q in observed_quests})
    state_source = os.getenv("LUMAVUN_STATE_SOURCE", "").strip() or "local"
    total_found = len(campaigns)

    new_campaigns, state, duplicates_skipped = deduplicate_campaigns(
        campaigns,
        state,
        max_entries=int(config.get("dedup", {}).get("max_entries", 200)),
        stale_days=int(config.get("dedup", {}).get("stale_days", 7)),
    )

    # Retry eligible unsent opportunities even when they have already been seen.
    to_notify = [
        campaign
        for campaign in {c.campaign_id: c for c in campaigns}.values()
        if campaign.evidence_verified and campaign.zero_capital_ok is True
        and queue.get(campaign.campaign_id, {}).get("status") in ("READY", "NEEDS_APPROVAL")
        and queue.get(campaign.campaign_id, {}).get("qualification", {}).get("status") == "ACTIONABLE"
        and not was_notified(state, campaign.campaign_id)
    ]
    logger.info(
        "Dedup: %d new, %d duplicates skipped, %d to notify",
        len(new_campaigns),
        len(duplicates_skipped),
        len(to_notify),
    )

    wallet = read_wallet(
        os.getenv("PHANTOM_PUBLIC_ADDRESS", "").strip(),
        os.getenv("SOLANA_RPC_URL", "").strip()
        or (config.get("rpc", {}).get("default_url", "") or ""),
        _env_list("SOLANA_RPC_FALLBACK_URLS"),
    )
    if wallet.get("address_valid") is False:
        logger.warning("Wallet: %s", wallet.get("diagnostics"))
        if os.getenv("PHANTOM_PUBLIC_ADDRESS", "").strip():
            # A configured but unusable address is a configuration error
            # worth surfacing loudly in the log and the report.
            logger.error(
                "PHANTOM_PUBLIC_ADDRESS is set but is not a valid Solana public "
                "key (Base58, 32 bytes). SOL balance cannot be read."
            )

    reward_tracking = update_rewards(state, wallet, os.getenv("PHANTOM_PUBLIC_ADDRESS", "").strip(), config=quest_settings.get("reward_tracking", {}))
    errors.extend(reward_tracking.get("errors", []) if wallet.get("address_valid") else [])

    from source_coverage import coverage_report
    coverage = coverage_report(specs, source_health, queue, previous_ids, reward_tracking)
    recovery = recovery_report(queue, reward_tracking)
    from pathlib import Path
    import json
    Path("source_coverage.json").write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")
    Path("opportunity_recovery.json").write_text(json.dumps(recovery, indent=2, ensure_ascii=False), encoding="utf-8")
    discovery_diagnostics = discovery_report(observed_quests, source_health, queue, previous_ids, reward_tracking)
    from qualification import shortlist
    quest_summary["shortlist"] = shortlist({k:v for k,v in queue.items() if k in {q["id"] for q in observed_quests}})
    quest_summary["discovery_statistics"] = discovery_diagnostics["statistics"]

    telegram_result: Optional[Dict[str, Any]] = None
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        batches = []
        summary_id = "summary:" + scan_time[:10]
        if not was_notified(state, summary_id):
            batches.append((summary_id, [build_daily_summary(
                scan_time, wallet, [campaign_payload(c) for c in campaigns],
                source_health, errors, total_found, len(duplicates_skipped), quest_summary, reward_tracking
            )]))
        for campaign in to_notify:
            batches.append((campaign.campaign_id, chunk_message(build_opportunity_block({**campaign_payload(campaign), "quest": queue.get(campaign.campaign_id)}, 1))))
        telegram_result = {"sent": True, "messages": 0, "sent_count": 0, "error": None}
        delivery_errors = []
        for ident, batch in batches:
            result = send_messages(bot_token, chat_id, batch)
            telegram_result["messages"] += result["messages"]
            telegram_result["sent_count"] += result["sent_count"]
            if result.get("sent"):
                record_notification(state, ident)
                save_state_locally(state)
            else:
                telegram_result["sent"] = False
                delivery_errors.append(str(result.get("error") or "Delivery failed"))
        telegram_result["error"] = "; ".join(delivery_errors) or None
    else:
        logger.info("Telegram not configured; printing the summary only")
        print(build_daily_summary(scan_time, wallet, [campaign_payload(c) for c in campaigns], source_health, errors, total_found, len(duplicates_skipped), quest_summary, reward_tracking))

    mark_run(state)
    save_state_locally(state)

    report = build_report(
        scan_time=scan_time,
        campaigns=campaigns,
        source_health=source_health,
        wallet=wallet,
        errors=errors,
        telegram_result=telegram_result,
        duplicates_skipped=len(duplicates_skipped),
        total_found=total_found,
        state_source=state_source,
        quest_queue=queue, adapter_health=adapter_health, reward_tracking=reward_tracking,
        discovery_diagnostics=discovery_diagnostics, recovery_report=recovery, source_coverage=coverage,
    )
    json_path = write_json_report(report)
    md_path = write_markdown_report(report)

    logger.info(
        "Report written: %s, %s | campaigns=%d new=%d duplicates=%d errors=%d telegram=%s",
        json_path,
        md_path,
        len(campaigns),
        len(new_campaigns),
        len(duplicates_skipped),
        len(errors),
        (telegram_result or {}).get("sent"),
    )
    for err in errors:
        print(f"WARN: {err}")
    if wallet.get("diagnostics"):
        print(f"WARN: wallet: {wallet['diagnostics']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
