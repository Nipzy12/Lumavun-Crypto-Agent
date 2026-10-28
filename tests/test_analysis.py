"""Tests for the V1.3 analysis engine.

Covers extraction, opportunity classification, the zero-capital filter,
risk scoring and cross-checking - including the cases where the source says
nothing, because "not stated" must stay "not stated".
"""

from datetime import date

import pytest

from analysis import (
    classify_opportunity,
    cross_check_campaigns,
    domain_consistency,
    evaluate_zero_capital,
    extract_deadline,
    extract_eligibility,
    extract_reward,
    extract_tasks,
    normalize,
    project_key,
    score_risk,
    sentences,
)


class TestTextHelpers:
    def test_normalize_collapses_whitespace(self):
        assert normalize("  a \n\t b  ") == "a b"

    def test_normalize_of_none_is_empty(self):
        assert normalize(None) == ""
        assert normalize("") == ""

    def test_sentences_splits_on_punctuation_and_newlines(self):
        assert len(sentences("First line.\nSecond line! Third?")) == 3


class TestDeadlineExtraction:
    def test_iso_date_after_deadline_word(self):
        result = extract_deadline(
            "The campaign ends on 2026-11-30 for early participants."
        )
        assert result is not None
        assert result.iso == "2026-11-30"
        assert "ends on" in result.text.lower()

    def test_day_month_year_format(self):
        result = extract_deadline("Snapshot is taken by 31 October 2026.")
        assert result is not None
        assert result.iso == "2026-10-31"

    def test_month_day_year_format(self):
        result = extract_deadline("Registration closes on October 31, 2026.")
        assert result is not None
        assert result.iso == "2026-10-31"

    def test_no_deadline_word_returns_none(self):
        assert extract_deadline("The token launches on 2026-11-30.") is None

    def test_relative_deadline_is_not_invented(self):
        assert extract_deadline("Two weeks until the airdrop ends.") is None

    def test_absent_deadline_returns_none(self):
        assert extract_deadline("") is None
        assert extract_deadline("Join the quest and earn points.") is None

    def test_unreasonably_distant_date_is_ignored(self):
        assert extract_deadline("Ends on 2099-01-01.") is None


class TestEligibilityAndTasks:
    def test_eligibility_quotes_matching_sentences(self):
        found = extract_eligibility(
            "You must hold 100 tokens. Wallet must be at least 30 days old."
        )
        assert len(found) >= 2
        assert any("hold 100" in item.lower() for item in found)

    def test_eligibility_is_empty_when_not_stated(self):
        assert extract_eligibility("Nothing about who can join.") == []

    def test_tasks_quote_action_sentences(self):
        found = extract_tasks(
            "Follow the official account. Join the discord and submit your address."
        )
        assert len(found) >= 2
        assert any("follow" in item.lower() for item in found)

    def test_tasks_are_empty_when_not_stated(self):
        assert extract_tasks("No instructions were published.") == []

    def test_tasks_are_bounded(self):
        text = ". ".join(f"Complete step {i} now" for i in range(20))
        assert len(extract_tasks(text, limit=3)) <= 3


class TestRewardExtraction:
    def test_stated_amount_is_reported(self):
        info = extract_reward(
            "The genesis airdrop will distribute 4.4 billion $FLOP to participants.",
            _config(),
        )
        assert info.status == "stated"
        assert "4.4" in info.text
        assert info.text is not None

    def test_percentage_counts_as_stated(self):
        info = extract_reward("Total reward: 15% of supply is reserved.", _config())
        assert info.status == "stated"

    def test_points_only_is_never_described_as_a_token_reward(self):
        info = extract_reward(
            "Earn points on the leaderboard for each weekly season.", _config()
        )
        assert info.status == "points_only"
        assert "janji token" in info.note

    def test_speculative_reward_is_labelled_speculative(self):
        info = extract_reward(
            "Participants may receive a potential airdrop later.", _config()
        )
        assert info.status == "speculative"

    def test_missing_reward_is_unspecified(self):
        info = extract_reward("Just a general announcement about the launch.", _config())
        assert info.status == "unspecified"
        assert info.text is None
        assert "tidak menyebut" in info.note


def _config():
    from config_loader import load_config

    return load_config()


class TestOpportunityClassification:
    def test_confirmed_airdrop_needs_airdrop_and_confirmation(self):
        result = classify_opportunity(
            "Meta Chain Airdrop",
            "The snapshot is confirmed and distribution is eligible for holders.",
            _config(),
        )
        assert result.type == "CONFIRMED_AIRDROP"
        assert "airdrop" in result.reason.lower()

    def test_airdrop_without_confirmation_is_unverified(self):
        result = classify_opportunity(
            "Mystery Token Airdrop", "Something is coming soon.", _config()
        )
        assert result.type == "UNVERIFIED"
        assert "tanpa bukti konfirmasi" in result.reason

    def test_speculation_downgrades_to_unverified(self):
        result = classify_opportunity(
            "Foo Airdrop",
            "The team proposes distributing tokens in a potential reward.",
            _config(),
        )
        assert result.type == "UNVERIFIED"
        assert "spekulasi" in result.reason

    def test_points_program(self):
        result = classify_opportunity(
            "Quip Points",
            "Climb the leaderboard this season and collect points.",
            _config(),
        )
        assert result.type == "POINTS_PROGRAM"
        assert "poin" in result.reason.lower()

    def test_points_program_is_not_called_an_airdrop(self):
        result = classify_opportunity(
            "Quip Points", "Climb the leaderboard this season.", _config()
        )
        assert result.type != "CONFIRMED_AIRDROP"

    def test_testnet_potential(self):
        result = classify_opportunity(
            "Hoodi Testnet",
            "Run a node on the testnet and request faucet funds.",
            _config(),
        )
        assert result.type == "TESTNET_POTENTIAL"

    def test_free_quest(self):
        result = classify_opportunity(
            "Community Quest",
            "Follow the account and join the discord to complete the quest.",
            _config(),
        )
        assert result.type == "FREE_QUEST"

    def test_downgrade_phrase_blocks_confirmed(self):
        result = classify_opportunity(
            "Foo Airdrop",
            "Airdrop confirmed, but no token will be distributed yet.",
            _config(),
        )
        assert result.type == "UNVERIFIED"

    def test_unclassifiable_text_is_unverified(self):
        result = classify_opportunity("General Market News", "Prices moved today.", _config())
        assert result.type == "UNVERIFIED"
        assert result.matched_signals == []


class TestZeroCapitalFilter:
    def test_deposit_blocker_fails_the_filter(self):
        result = evaluate_zero_capital(
            "Scam Drop", "Send funds to claim your guaranteed profit.", _config()
        )
        assert result.ok is False
        assert any("send funds" in b for b in result.blockers)

    def test_negated_blocker_is_not_counted(self):
        result = evaluate_zero_capital(
            "Community Quest", "No deposit required to join this quest.", _config()
        )
        assert result.ok is True
        assert result.blockers == []

    def test_purchase_blocker_fails_the_filter(self):
        result = evaluate_zero_capital(
            "Token Sale", "You must buy tokens in the presale to qualify.", _config()
        )
        assert result.ok is False

    def test_onchain_action_without_gas_cost_is_unknown(self):
        result = evaluate_zero_capital(
            "Bridge Drop", "Bridge your tokens to the testnet to qualify.", _config()
        )
        assert result.ok is None
        assert result.unknown_costs
        assert "gas" in result.unknown_costs[0].lower()

    def test_onchain_action_with_free_gas_passes(self):
        result = evaluate_zero_capital(
            "Faucet Quest", "Use the faucet to bridge; gas is free.", _config()
        )
        assert result.ok is True

    def test_offchain_actions_pass(self):
        result = evaluate_zero_capital(
            "Social Quest", "Follow the account and join the discord.", _config()
        )
        assert result.ok is True
        assert result.offchain_actions

    def test_no_cost_language_defaults_to_pass(self):
        result = evaluate_zero_capital("Incentive Testnet", "Run a validator node.", _config())
        assert result.ok is True
        assert result.reason

    def test_missing_text_does_not_crash(self):
        result = evaluate_zero_capital("", "", _config())
        assert result.ok is True


class TestRiskScoring:
    def test_no_flags_scores_zero(self):
        result = score_risk([], _config())
        assert result.score == 0
        assert result.band == "low"
        assert result.explanation

    def test_flags_add_up_with_readable_trail(self):
        result = score_risk(["deposit_required", "secret_request"], _config())
        assert result.score == 75
        assert result.band == "high"
        assert any("deposit" in line.lower() for line in result.explanation)
        assert any("+30" in line for line in result.explanation)

    def test_score_is_capped(self):
        flags = ["deposit_required", "secret_request", "guaranteed_profit", "drain_request"]
        assert score_risk(flags, _config()).score == 100

    def test_extra_flag_contributes_and_is_explained(self):
        result = score_risk(["domain_mismatch"], _config(), extra_flags={
            "domain_mismatch": "claim.evil.io tidak sama dengan situs soei.io",
        })
        assert result.score == 20
        assert any("claim.evil.io" in line for line in result.explanation)

    def test_band_boundaries(self):
        assert score_risk(["unknown_project"], _config()).band in ("low", "medium")
        assert score_risk(["suspicious_url"], _config()).score == 15


class TestCrossChecking:
    def _campaign(self, title, source, official=False, project=None):
        from discovery import Campaign

        return Campaign(
            title=title,
            project=project or title,
            url="https://example.com/x",
            source=source,
            source_is_official=official,
        )

    def test_single_source_is_never_marked_verified(self):
        campaign = self._campaign("Alpha Airdrop", "https://a.example/feed")
        cross_check_campaigns([campaign])
        assert campaign.cross_checked is False
        assert "satu sumber" in campaign.cross_check_note

    def test_two_distinct_hosts_mark_cross_checked(self):
        campaign = self._campaign("Alpha Airdrop", "https://a.example/feed")
        same = self._campaign("Alpha Airdrop", "https://b.example/feed")
        cross_check_campaigns([campaign, same])
        assert campaign.cross_checked is True
        assert same.cross_checked is True
        assert "2 sumber" in campaign.cross_check_note

    def test_official_source_participation_is_noted(self):
        campaign = self._campaign("Alpha Airdrop", "https://a.example/feed")
        official = self._campaign(
            "Alpha Airdrop", "https://blog.official.example/rss", official=True
        )
        cross_check_campaigns([campaign, official])
        assert "resmi" in campaign.cross_check_note

    def test_same_source_twice_is_not_cross_checked(self):
        campaign = self._campaign("Alpha Airdrop", "https://a.example/feed")
        again = self._campaign("Alpha Airdrop", "https://a.example/feed")
        cross_check_campaigns([campaign, again])
        assert campaign.cross_checked is False

    def test_project_key_is_normalized(self):
        # Stopwords (including "airdrop"/"airdrops") are dropped and the key is
        # limited to three tokens so matching stays stable.
        assert project_key("", "Best Airdrops to Farm Right Now") == "farm right now"
        assert project_key("Quip Network", "") == "quip network"
        assert project_key("", "  The   FLOP  Airdrop!") == "flop"

    def test_domain_consistency_accepts_same_registrable_domain(self):
        ok, note = domain_consistency("https://proof.soei.io/?ref=x", "https://soei.io/")
        assert ok is True
        assert note == ""

    def test_domain_consistency_flags_a_mismatch(self):
        ok, note = domain_consistency("https://claim.other.io/x", "https://soei.io/")
        assert ok is False
        # The note compares registrable domains, not full hosts.
        assert "other.io" in note
        assert "soei.io" in note

    def test_domain_consistency_compares_registrable_domains(self):
        # Different subdomain, same registrable domain: not a mismatch.
        ok, note = domain_consistency("https://proof.soei.io/x", "https://www.soei.io/")
        assert ok is True
        assert note == ""

    def test_domain_consistency_tolerates_missing_urls(self):
        assert domain_consistency("", "") == (True, "")
        assert domain_consistency("https://a.io/", "") == (True, "")
