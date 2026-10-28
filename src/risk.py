"""
Rule-based airdrop risk scanner.

This module provides a transparent scoring system. Classifications are
based only on extracted text from the feeds you provide. A low-risk
classification never implies guaranteed safety.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from discovery import Campaign


def classify(campaign: Campaign, config: Dict[str, Any]) -> Campaign:
    """Re-derive campaign classification from risk rules.

    This exists so other modules can recompute classification consistently
    without depending on discovery internals.
    """
    high_ids = set(
        str(i)
        for i in config.get("classification", {}).get(
            "high_risk_indicator_ids", []
        )
    )
    moderate_ids = set(
        str(i)
        for i in config.get("classification", {}).get(
            "moderate_indicator_ids", []
        )
    )

    high_hits = [f for f in campaign.risk_flags if f in high_ids]
    moderate_hits = [f for f in campaign.risk_flags if f in moderate_ids]

    if high_hits:
        campaign.classification = "HIGH_RISK_INDICATORS"
    elif moderate_hits:
        campaign.classification = "REVIEW_REQUIRED"
    else:
        campaign.classification = "LOW_RISK_INDICATORS"
    return campaign


def summarize_classification(
    campaigns: Sequence[Campaign],
) -> Dict[str, Any]:
    """Produce a summary of classification counts."""
    counts: Dict[str, int] = {
        "LOW_RISK_INDICATORS": 0,
        "REVIEW_REQUIRED": 0,
        "HIGH_RISK_INDICATORS": 0,
    }
    for campaign in campaigns:
        counts[campaign.classification] = counts.get(
            campaign.classification, 0
        ) + 1
    return {
        "total": len(list(campaigns)),
        "by_classification": counts,
    }
