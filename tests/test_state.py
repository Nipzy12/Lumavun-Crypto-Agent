"""Tests for persistent dedup state across workflow runs.

These tests model the real workflow: run 1 starts with no state, saves it,
and run 2 loads that saved state and must treat the same campaigns as
duplicates. The state file is confined to a temporary directory so tests
never touch the repository or a real ``dedup_state.json``.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from discovery import Campaign, deduplicate_campaigns
from state import (
    STATE_FILENAME,
    _sanitize_state,
    default_state,
    load_state_from_artifact,
    save_state_locally,
)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """Isolate each simulated run in its own working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def campaign(title="Example Airdrop", url="https://example.com/claim", source="https://feed.example.com/rss"):
    return Campaign(title=title, project="Example", url=url, source=source)


class TestStateRoundTrip:
    def test_first_run_has_no_prior_state(self, run_dir):
        state = load_state_from_artifact()
        assert state["seen"] == {}
        assert state["version"] == "1.2"

    def test_saved_state_is_reloaded_by_the_next_run(self, run_dir):
        state = default_state()
        state["seen"]["abc"] = {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "title": "Example Airdrop",
            "source": "https://feed.example.com/rss",
            "classification": "REVIEW_REQUIRED",
        }
        path = save_state_locally(state)
        assert path.exists()
        assert path.name == STATE_FILENAME

        # Simulate the next runner starting fresh and downloading the file.
        reloaded = load_state_from_artifact()
        assert "abc" in reloaded["seen"]
        assert reloaded["seen"]["abc"]["title"] == "Example Airdrop"

    def test_corrupt_state_file_falls_back_to_empty(self, run_dir):
        Path(STATE_FILENAME).write_text("{not valid json", encoding="utf-8")
        state = load_state_from_artifact()
        assert state["seen"] == {}

    def test_non_dict_state_falls_back_to_empty(self, run_dir):
        Path(STATE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        state = load_state_from_artifact()
        assert state["seen"] == {}

    def test_sanitize_drops_unexpected_value_types(self):
        dirty = {
            "version": "1.2",
            "seen": {
                "keep": {"last_seen": "2026-01-01", "title": "ok"},
                "drop-nonnested": "not-a-dict",
                "partial": {"last_seen": "2026-01-01", "weird": {"nested": 1}},
            },
        }
        clean = _sanitize_state(dirty)
        assert set(clean["seen"]) == {"keep", "partial"}
        assert "weird" not in clean["seen"]["partial"]

    def test_state_file_contains_no_secrets(self, run_dir):
        state = default_state()
        state["seen"]["k"] = {"last_seen": "2026-01-01", "title": "t"}
        save_state_locally(state)
        raw = Path(STATE_FILENAME).read_text(encoding="utf-8")
        lowered = raw.lower()
        for forbidden in ("token", "chat_id", "private", "secret", "seed"):
            assert forbidden not in lowered


class TestTwoConsecutiveRuns:
    """Run 1 discovers campaigns, run 2 must skip them as duplicates."""

    def test_run_two_skips_campaigns_seen_in_run_one(self, run_dir):
        campaigns = [
            campaign("Alpha Airdrop", "https://ex.com/a"),
            campaign("Beta Testnet", "https://ex.com/b"),
        ]

        # ---- Run 1: no state yet ----
        state = load_state_from_artifact()
        new1, state_after_1, dupes1 = deduplicate_campaigns(campaigns, state)
        assert len(new1) == 2
        assert len(dupes1) == 0
        save_state_locally(state_after_1)

        # ---- Run 2: fresh runner, loads run 1's uploaded state ----
        state_2 = load_state_from_artifact()
        assert len(state_2["seen"]) == 2

        new2, state_after_2, dupes2 = deduplicate_campaigns(campaigns, state_2)
        assert len(new2) == 0, "second run must not re-report run 1 campaigns"
        assert len(dupes2) == 2
        save_state_locally(state_after_2)

    def test_run_two_reports_genuinely_new_campaign(self, run_dir):
        first = [campaign("Alpha Airdrop", "https://ex.com/a")]

        state = load_state_from_artifact()
        _, state, _ = deduplicate_campaigns(first, state)
        save_state_locally(state)

        # Run 2 sees the old campaign plus one it has never seen.
        second = first + [campaign("Gamma Quest", "https://ex.com/c")]
        state_2 = load_state_from_artifact()
        new2, state_2, dupes2 = deduplicate_campaigns(second, state_2)

        titles = [c.title for c in new2]
        assert titles == ["Gamma Quest"]
        assert len(dupes2) == 1
        assert len(state_2["seen"]) == 2

    def test_stale_entry_reappears_after_stale_days(self, run_dir):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        state = default_state()
        target = campaign("Alpha Airdrop", "https://ex.com/a")
        from discovery import _dedup_key

        state["seen"][_dedup_key(target)] = {
            "last_seen": old,
            "title": target.title,
            "source": target.source,
            "classification": "REVIEW_REQUIRED",
        }

        new, _, dupes = deduplicate_campaigns([target], state, stale_days=7)
        assert len(new) == 1
        assert len(dupes) == 0

    def test_fresh_entry_stays_deduplicated(self, run_dir):
        state = default_state()
        target = campaign("Alpha Airdrop", "https://ex.com/a")
        from discovery import _dedup_key

        state["seen"][_dedup_key(target)] = {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "title": target.title,
            "source": target.source,
            "classification": "REVIEW_REQUIRED",
        }

        new, _, dupes = deduplicate_campaigns([target], state, stale_days=7)
        assert len(new) == 0
        assert len(dupes) == 1
