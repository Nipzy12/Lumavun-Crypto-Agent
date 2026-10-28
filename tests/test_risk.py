"""Tests for the risk module (V1.3): tiers, summaries, consistency.

Risk tiers must have exactly one implementation, shared by the discovery
engine, so this file also asserts that the two agree.
"""

from discovery import Campaign, _classify_risk
from risk import classify, risk_tier, summarize_classification


def _config():
    from config_loader import load_config

    return load_config()


def _campaign(flags, title="Drop", url="https://example.com/x"):
    campaign = Campaign(
        title=title,
        project=title,
        url=url,
        source="https://feed.example/rss",
        risk_flags=list(flags),
    )
    return campaign


class TestRiskTier:
    def test_no_flags_is_low_risk(self):
        assert risk_tier([], _config()) == "LOW_RISK_INDICATORS"

    def test_high_severity_flag_wins(self):
        assert risk_tier(["secret_request"], _config()) == "HIGH_RISK_INDICATORS"
        assert risk_tier(["deposit_required"], _config()) == "HIGH_RISK_INDICATORS"
        assert risk_tier(["drain_request"], _config()) == "HIGH_RISK_INDICATORS"
        assert risk_tier(["guaranteed_profit"], _config()) == "HIGH_RISK_INDICATORS"

    def test_moderate_flag_means_review(self):
        assert risk_tier(["suspicious_url"], _config()) == "REVIEW_REQUIRED"
        assert risk_tier(["unverifiable_reward"], _config()) == "REVIEW_REQUIRED"
        assert risk_tier(["domain_mismatch"], _config()) == "REVIEW_REQUIRED"

    def test_high_beats_moderate(self):
        assert risk_tier(["suspicious_url", "secret_request"], _config()) == "HIGH_RISK_INDICATORS"

    def test_unknown_flags_are_low_not_ignored_silently(self):
        # An unweighted flag must not crash or silently escalate the tier.
        assert risk_tier(["not_a_real_rule"], _config()) == "LOW_RISK_INDICATORS"

    def test_empty_config_defaults_to_low(self):
        assert risk_tier([], {}) == "LOW_RISK_INDICATORS"


class TestClassify:
    def test_classify_sets_the_tier_on_the_campaign(self):
        campaign = _campaign(["deposit_required"])
        classify(campaign, _config())
        assert campaign.classification == "HIGH_RISK_INDICATORS"

    def test_classify_returns_the_same_campaign(self):
        campaign = _campaign([])
        assert classify(campaign, _config()) is campaign

    def test_classify_is_idempotent(self):
        campaign = _campaign(["suspicious_url"])
        first = classify(campaign, _config()).classification
        second = classify(campaign, _config()).classification
        assert first == second == "REVIEW_REQUIRED"

    def test_risk_flags_and_tier_stay_independent_fields(self):
        campaign = _campaign(["suspicious_url"])
        classify(campaign, _config())
        assert campaign.risk_flags == ["suspicious_url"]
        assert campaign.classification == "REVIEW_REQUIRED"


class TestDiscoveryConsistency:
    """The discovery engine must use this module's tier logic, not its own."""

    @staticmethod
    def _all_ids():
        config = _config()
        return sorted(
            set(config["classification"]["high_risk_indicator_ids"])
            | set(config["classification"]["moderate_indicator_ids"])
        )

    def test_every_configured_indicator_maps_identically(self):
        config = _config()
        for flag in self._all_ids():
            assert _classify_risk([flag], config) == risk_tier([flag], config), flag

    def test_combinations_map_identically(self):
        config = _config()
        ids = self._all_ids()
        # Single flags plus every pair.
        for i, a in enumerate(ids):
            for b in ids[i:]:
                assert _classify_risk([a, b], config) == risk_tier([a, b], config), (a, b)


class TestSummarize:
    def test_counts_are_per_tier(self):
        campaigns = [
            _campaign([], "A"),
            _campaign(["suspicious_url"], "B"),
            _campaign(["secret_request"], "C"),
            _campaign(["deposit_required"], "D"),
        ]
        for campaign in campaigns:
            classify(campaign, _config())
        summary = summarize_classification(campaigns)
        assert summary["total"] == 4
        assert summary["by_classification"] == {
            "LOW_RISK_INDICATORS": 1,
            "REVIEW_REQUIRED": 1,
            "HIGH_RISK_INDICATORS": 2,
        }

    def test_empty_summary_is_zero_not_missing(self):
        summary = summarize_classification([])
        assert summary["total"] == 0
        assert set(summary["by_classification"]) == {
            "LOW_RISK_INDICATORS",
            "REVIEW_REQUIRED",
            "HIGH_RISK_INDICATORS",
        }

    def test_summary_always_contains_every_tier_key(self):
        campaigns = [_campaign(["drain_request"])]
        classify(campaigns[0], _config())
        summary = summarize_classification(campaigns)
        assert set(summary["by_classification"]) == {
            "LOW_RISK_INDICATORS",
            "REVIEW_REQUIRED",
            "HIGH_RISK_INDICATORS",
        }
