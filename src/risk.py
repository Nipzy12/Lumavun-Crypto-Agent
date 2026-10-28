"""
Rule-based airdrop risk scanner.

This module is the single source of truth for the risk *tier* a campaign
gets (`LOW_RISK_INDICATORS`, `REVIEW_REQUIRED`, `HIGH_RISK_INDICATORS`).
The numeric score and its readable trail live in :mod:`analysis`.

Classifications are based only on extracted text from the sources you
provide. A low-risk classification never implies guaranteed safety, and no
output of this module is a security guarantee.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from discovery import Campaign


def _tier_ids(config: Dict[str, Any]) -> tuple[set, set]:
    classification = config.get("classification", {}) or {}
    high = {str(i) for i in (classification.get("high_risk_indicator_ids", []) or [])}
    moderate = {str(i) for i in (classification.get("moderate_indicator_ids", []) or [])}
    return high, moderate


def risk_tier(flags: Sequence[str], config: Dict[str, Any]) -> str:
    """Map risk flags to a tier.

    Any high-severity indicator wins outright; a moderate indicator means
    the entry needs a human look; otherwise there are simply fewer matched
    red-flag phrases.
    """
    high, moderate = _tier_ids(config)
    flags = list(flags)
    if any(flag in high for flag in flags):
        return "HIGH_RISK_INDICATORS"
    if any(flag in moderate for flag in flags):
        return "REVIEW_REQUIRED"
    return "LOW_RISK_INDICATORS"


def classify(campaign: "Campaign", config: Dict[str, Any]) -> "Campaign":
    """Re-derive the campaign's risk tier from its flags.

    This exists so other modules can recompute classification consistently
    without depending on discovery internals.
    """
    campaign.classification = risk_tier(campaign.risk_flags, config)
    return campaign


def summarize_classification(campaigns: Sequence["Campaign"]) -> Dict[str, Any]:
    """Produce a summary of tier counts."""
    counts: Dict[str, int] = {
        "LOW_RISK_INDICATORS": 0,
        "REVIEW_REQUIRED": 0,
        "HIGH_RISK_INDICATORS": 0,
    }
    total = 0
    for campaign in campaigns:
        total += 1
        tier = campaign.classification
        counts[tier] = counts.get(tier, 0) + 1
    return {"total": total, "by_classification": counts}
