"""
LUMAVUN Crypto Agent V1.1 — entrypoint.

This orchestrator composes the discovery engine, risk scanner, Phantom
wallet monitor, Telegram reporter, persistent state, and report writers.
It is designed to degrade gracefully: if one integration such as Telegram
fails, the agent still writes its reports and finishes.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from discovery import (
    Campaign,
    SourceHealth,
    deduplicate_campaigns,
    scan_feeds,
)
from config_loader import effective_feeds, load_config
from reporter import build_report, write_json_report, write_markdown_report
from risk import classify, summarize_classification
from state import load_state_from_artifact, save_state_locally
from telegram import (
    build_report_message,
    notify,
    should_notify_campaign,
)
from wallet import read_wallet

logging.basicConfig(
    level=os.getenv("LUMAVUN_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lumavun")

REQUEST_TIMEOUT = int(os.getenv("LUMAVUN_REQUEST_TIMEOUT", "20"))


def _env_list(name: str) -> List[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    logger.info("LUMAVUN Crypto Agent V1.1 starting")
    scan_time = datetime.now(timezone.utc).isoformat()
    config = load_config()

    feeds = effective_feeds()
    logger.info("Feeds to scan: %d", len(feeds))

    campaigns, source_health, errors = scan_feeds(
        feeds, config=config
    )

    # Risk classification and dedup are computed after scanning.
    for campaign in campaigns:
        classify(campaign, config)

    state = load_state_from_artifact()
    campaigns, state, duplicates_skipped = deduplicate_campaigns(
        campaigns,
        state,
        max_entries=config.get("dedup", {}).get("max_entries", 200),
        stale_days=config.get("dedup", {}).get("stale_days", 7),
    )
    save_state_locally(state)

    wallet = read_wallet(
        os.getenv("PHANTOM_PUBLIC_ADDRESS", "").strip(),
        os.getenv("SOLANA_RPC_URL", "").strip() or (
            config.get("rpc", {}).get("default_url", "") or ""
        ),
        _env_list("SOLANA_RPC_FALLBACK_URLS"),
    )

    # Only notify campaigns that are new relative to the previous run.
    previous_seen_keys = set(state.get("seen", {}).keys())
    notified_hashes: List[str] = []
    campaigns_to_notify: List[Dict[str, Any]] = []
    for campaign in campaigns:
        payload = {
            "title": campaign.title,
            "url": campaign.url,
            "source": campaign.source,
            "risk_flags": campaign.risk_flags,
            "classification": campaign.classification,
            "is_confirmed": campaign.is_confirmed,
            "is_speculation": campaign.is_speculation,
        }
        if should_notify_campaign(payload, notified_hashes):
            campaigns_to_notify.append(payload)
            notified_hashes.append(
                hashlib_helper(payload)
            )

    message = build_report_message(
        scan_time=scan_time,
        wallet_summary=wallet,
        campaigns=campaigns_to_notify,
        source_health=source_health,
        errors=errors,
    )

    telegram_result: Optional[Dict[str, Any]] = None
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        telegram_result = notify(bot_token, chat_id, message)
        if not telegram_result.get("sent"):
            logger.warning(
                "Telegram notification did not send: %s",
                telegram_result.get("error"),
            )
    else:
        logger.info("Telegram not configured; printing report summary only")
        print(message)

    report = build_report(
        scan_time=scan_time,
        campaigns=campaigns,
        source_health=source_health,
        wallet=wallet,
        errors=errors,
        telegram_result=telegram_result,
        duplicates_skipped=len(duplicates_skipped),
    )

    json_path = write_json_report(report)
    md_path = write_markdown_report(report)

    logger.info(
        "Report written: %s, %s | campaigns=%d errors=%d telegram_sent=%s",
        json_path,
        md_path,
        len(campaigns),
        len(errors),
        telegram_result.get("sent") if telegram_result else None,
    )
    for err in errors:
        print(f"WARN: {err}")
    if wallet.get("diagnostics"):
        print(f"WARN: wallet: {wallet['diagnostics']}")
    return 0


def hashlib_helper(payload: Dict[str, Any]) -> str:
    """Small helper to avoid a top-level import of hashlib in main."""
    import hashlib

    parts = [
        str(payload.get("title", "")).strip().lower(),
        str(payload.get("source", "")).strip().lower(),
        str(payload.get("url", "")).strip().lower(),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    sys.exit(main())
