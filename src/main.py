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
from telegram import build_messages, send_messages
from wallet import read_wallet

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

AGENT_VERSION = "1.3"


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

    state = load_state_from_artifact()
    state_source = os.getenv("LUMAVUN_STATE_SOURCE", "").strip() or "local"
    total_found = len(campaigns)

    new_campaigns, state, duplicates_skipped = deduplicate_campaigns(
        campaigns,
        state,
        max_entries=int(config.get("dedup", {}).get("max_entries", 200)),
        stale_days=int(config.get("dedup", {}).get("stale_days", 7)),
    )

    # Only notify what is new AND has never been sent. State survives runs.
    to_notify = [
        campaign
        for campaign in new_campaigns
        if not was_notified(state, campaign.campaign_id)
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

    payloads = [campaign_payload(campaign) for campaign in to_notify]
    messages = build_messages(
        scan_time=scan_time,
        wallet_summary=wallet,
        campaigns=payloads,
        source_health=source_health,
        errors=errors,
        total_found=total_found,
        duplicates_skipped=len(duplicates_skipped),
    )

    telegram_result: Optional[Dict[str, Any]] = None
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        telegram_result = send_messages(bot_token, chat_id, messages)
        if not telegram_result.get("sent"):
            logger.warning(
                "Telegram delivery incomplete: %s", telegram_result.get("error")
            )
    else:
        logger.info("Telegram not configured; printing the summary only")
        print(messages[0] if messages else "No summary generated")
        telegram_result = None

    # Record notification history only when delivery actually happened, so a
    # failed send is retried on the next run instead of being lost.
    delivered = telegram_result is None or bool(telegram_result.get("sent"))
    if delivered:
        for campaign in to_notify:
            record_notification(
                state,
                campaign.campaign_id,
                opportunity_type=campaign.opportunity_type,
                title=campaign.title[:80],
            )

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
