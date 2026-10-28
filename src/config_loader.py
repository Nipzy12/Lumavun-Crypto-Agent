"""
Configuration loader for the vetted-sources YAML file.

The agent reads this file as the source registry plus rule reference. When
AIRDROP_RSS_FEEDS is set via GitHub Variables, those URLs replace the
configured registry (feed transport only), which keeps the V1.1/V1.2
behaviour available for operators who prefer an explicit list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "vetted_sources.yaml"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the vetted-sources configuration as a dict.

    Returns a default empty-ish config if the file is missing or invalid,
    so the agent degrades gracefully rather than crashing.
    """
    target = path or CONFIG_PATH
    default: Dict[str, Any] = {
        "version": "1.3",
        "default_feeds": [],
        "sources": [],
        "source_policy": {
            "user_agent": "LUMAVUN-Crypto-Agent/1.3",
            "respect_robots": True,
            "min_interval_seconds": 1.5,
            "request_timeout_seconds": 20,
            "max_entries_per_source": 15,
            "max_findings": 20,
        },
        "discovery": {
            "campaign_keywords": [],
            "confirmation_signals": [],
            "speculation_signals": [],
        },
        "opportunity": {"signals": {}, "downgrade_phrases": [], "reward_statements": {}},
        "zero_capital": {"blockers": [], "negations": [], "onchain_actions": []},
        "risk_rules": [],
        "risk_scoring": {
            "cap": 100,
            "bands": {"low_max": 20, "medium_max": 50},
            "weights": {},
        },
        "classification": {
            "high_risk_indicator_ids": [],
            "moderate_indicator_ids": [],
        },
        "rpc": {"default_url": "https://api.mainnet-beta.solana.com"},
        "dedup": {"max_entries": 200, "stale_days": 7},
    }
    if not target.exists():
        return default
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            return default
        return _deep_merge(default, data)
    except Exception:
        return default


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_list(name: str) -> List[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def effective_feeds() -> List[str]:
    """Return the feed URLs the agent should scan for this run.

    AIRDROP_RSS_FEEDS (GitHub Variables) takes precedence. If it is empty,
    the configured default_feeds are used.
    """
    env_feeds = _env_list("AIRDROP_RSS_FEEDS")
    if env_feeds:
        return env_feeds
    config = load_config()
    return [str(x).strip() for x in config.get("default_feeds", []) if str(x).strip()]


def effective_sources() -> List[Any]:
    """Return the SourceSpec list for this run.

    With AIRDROP_RSS_FEEDS set, the run reads exactly those feeds (feed
    transport). Otherwise it reads the full configured registry, which
    includes RSS feeds, official project feeds and JSON APIs.
    """
    from sources import specs_from_config, specs_from_urls

    env_feeds = _env_list("AIRDROP_RSS_FEEDS")
    if env_feeds:
        return specs_from_urls(env_feeds)
    return specs_from_config(load_config())


def known_sources() -> Dict[str, Any]:
    """Return the vetted-sources documentation block for reference."""
    config = load_config()
    return {
        "version": config.get("version", "1.3"),
        "default_feeds": config.get("default_feeds", []),
        "sources": config.get("sources", []),
        "verified_sources": config.get("verified_sources", []),
        "rejected_sources": config.get("rejected_sources", []),
        "source_policy": config.get("source_policy", {}),
        "discovery": config.get("discovery", {}),
        "risk_rules": config.get("risk_rules", []),
        "rpc": config.get("rpc", {}),
        "dedup": config.get("dedup", {}),
    }
