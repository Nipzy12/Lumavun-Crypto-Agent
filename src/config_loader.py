"""
Configuration loader for the vetted-sources YAML file.

The agent reads this file as a documentation and fallback reference. When
AIRDROP_RSS_FEEDS is set via GitHub Variables, those URLs take precedence.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "vetted_sources.yaml"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the vetted-sources configuration as a dict.

    Returns a default empty-ish config if the file is missing or invalid,
    so the agent degrades gracefully rather than crashing.
    """
    target = path or CONFIG_PATH
    default: Dict[str, Any] = {
        "version": "1.1",
        "default_feeds": [],
        "discovery": {
            "campaign_keywords": [],
            "confirmation_signals": [],
            "speculation_signals": [],
        },
        "risk_rules": [],
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
        merged = _deep_merge(default, data)
        return merged
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


def effective_feeds() -> list[str]:
    """Return the feeds the agent should scan for this run.

    AIRDROP_RSS_FEEDS (GitHub Variables) takes precedence. If it is empty,
    the configured default_feeds are used. Either way, feeds are validated
    before use.
    """
    env_feeds = os.getenv("AIRDROP_RSS_FEEDS", "").strip()
    if env_feeds:
        return [x.strip() for x in env_feeds.split(",") if x.strip()]
    config = load_config()
    return [str(x).strip() for x in config.get("default_feeds", []) if str(x).strip()]
