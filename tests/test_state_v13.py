"""V1.3 state tests: notification history, corruption, concurrency, merging."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from state import (
    CORRUPT_FILENAME,
    STATE_FILENAME,
    default_state,
    load_state_from_artifact,
    mark_run,
    merge_states,
    record_notification,
    save_state_locally,
    state_lock,
    was_notified,
)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _fresh_state():
    state = default_state()
    state["seen"]["campaign-1"] = {
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "title": "Alpha",
        "source": "https://feed.example/rss",
        "notified": False,
    }
    return state


class TestNotificationHistory:
    def test_record_marks_campaign_as_notified(self):
        state = default_state()
        state["seen"]["abc"] = {"last_seen": "2026-01-01T00:00:00+00:00"}
        record_notification(state, "abc", opportunity_type="CONFIRMED_AIRDROP")
        assert was_notified(state, "abc") is True
        assert state["seen"]["abc"]["notified"] is True
        assert state["notifications"]["abc"]["opportunity_type"] == "CONFIRMED_AIRDROP"

    def test_unrecorded_campaign_is_not_notified(self):
        state = default_state()
        assert was_notified(state, "missing") is False

    def test_notification_history_survives_a_round_trip(self, run_dir):
        state = default_state()
        record_notification(state, "campaign-1", title="Alpha")
        save_state_locally(state)
        reloaded = load_state_from_artifact()
        assert was_notified(reloaded, "campaign-1") is True

    def test_record_does_not_store_non_primitives(self):
        state = default_state()
        record_notification(state, "x", bad={"nested": "value"}, ok="text")
        record = state["notifications"]["x"]
        assert "bad" not in record
        assert record["ok"] == "text"

    def test_notification_history_is_bounded(self):
        state = default_state()
        for i in range(600):
            record_notification(state, f"id-{i}")
        merged = merge_states(default_state(), state)
        assert len(merged["notifications"]) <= 500


class TestCorruptionHandling:
    def test_corrupt_file_is_quarantined_and_run_continues(self, run_dir):
        Path(STATE_FILENAME).write_text("{ this is not json", encoding="utf-8")
        state = load_state_from_artifact()
        assert state["seen"] == {}
        assert state["version"] == "1.3"
        # Evidence is kept, not deleted.
        assert Path(CORRUPT_FILENAME).exists()
        assert not Path(STATE_FILENAME).exists()

    def test_non_dict_file_is_quarantined(self, run_dir):
        Path(STATE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        state = load_state_from_artifact()
        assert state["seen"] == {}
        assert Path(CORRUPT_FILENAME).exists()

    def test_saving_over_corrupt_state_recovers(self, run_dir):
        Path(STATE_FILENAME).write_text("garbage", encoding="utf-8")
        save_state_locally(_fresh_state())
        assert Path(STATE_FILENAME).exists()
        reloaded = load_state_from_artifact()
        assert "campaign-1" in reloaded["seen"]


class TestMerging:
    def test_union_keeps_history_from_both_sides(self):
        older = default_state()
        older["seen"]["a"] = {
            "last_seen": "2026-01-01T00:00:00+00:00",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "notified": False,
        }
        newer = default_state()
        newer["seen"]["a"] = {
            "last_seen": "2026-01-05T00:00:00+00:00",
            "first_seen": "2026-01-03T00:00:00+00:00",
            "notified": True,
        }
        newer["seen"]["b"] = {"last_seen": "2026-01-04T00:00:00+00:00"}

        merged = merge_states(older, newer)
        assert set(merged["seen"]) == {"a", "b"}
        assert merged["seen"]["a"]["first_seen"] == "2026-01-01T00:00:00+00:00"
        assert merged["seen"]["a"]["last_seen"] == "2026-01-05T00:00:00+00:00"
        assert merged["seen"]["a"]["notified"] is True

    def test_save_merges_with_state_already_on_disk(self, run_dir):
        first = default_state()
        first["seen"]["old"] = {"last_seen": "2026-01-01T00:00:00+00:00"}
        save_state_locally(first)

        second = default_state()
        second["seen"]["fresh"] = {"last_seen": "2026-01-02T00:00:00+00:00"}
        save_state_locally(second)

        reloaded = load_state_from_artifact()
        assert set(reloaded["seen"]) == {"old", "fresh"}

    def test_run_counter_only_grows(self):
        state = default_state()
        mark_run(state)
        mark_run(state)
        assert state["runs"] == 2
        merged = merge_states(default_state(), state)
        assert merged["runs"] == 2


class TestConcurrency:
    def test_lock_is_exclusive_and_released(self, run_dir):
        with state_lock():
            assert Path(STATE_FILENAME.replace(".json", ".lock")).exists()
        assert not Path(STATE_FILENAME.replace(".json", ".lock")).exists()

    def test_concurrent_saves_lose_no_records(self, run_dir):
        """Several overlapping runs must not erase each other's history."""
        errors = []

        def worker(index):
            try:
                state = default_state()
                state["seen"][f"campaign-{index}"] = {
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                    "title": f"Campaign {index}",
                }
                mark_run(state)
                save_state_locally(state)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        payload = json.loads(Path(STATE_FILENAME).read_text(encoding="utf-8"))
        # All six campaign records survive, plus at least one run counted.
        assert len([k for k in payload["seen"] if k.startswith("campaign-")]) == 6
        assert payload["runs"] >= 1
        assert payload["version"] == "1.3"

    def test_no_temporary_files_are_left_behind(self, run_dir):
        save_state_locally(_fresh_state())
        leftovers = [p.name for p in Path(".").iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestLegacyV12State:
    def test_v1_2_raw_key_still_deduplicates(self):
        """State written by V1.2 used raw title/source/url keys."""
        from discovery import Campaign, deduplicate_campaigns

        campaign = Campaign(
            title="Campaign One",
            project="Proj",
            url="https://example.com/c1",
            source="https://feeds.example.com/feed.xml",
        )
        legacy_key = "campaign one\x00https://feeds.example.com/feed.xml\x00https://example.com/c1"
        state = default_state()
        state["seen"][legacy_key] = {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "title": "Campaign One",
            "source": "https://feeds.example.com/feed.xml",
        }

        new, updated, dupes = deduplicate_campaigns([campaign], state, stale_days=7)
        assert len(new) == 0
        assert len(dupes) == 1
        # The record was upgraded to the stable id, with history preserved.
        assert legacy_key not in updated["seen"]
        assert len(updated["seen"]) == 1

    def test_v1_2_state_file_loads_cleanly(self, run_dir):
        legacy = {
            "version": "1.2",
            "updated_at": "2026-10-27T00:00:00+00:00",
            "seen": {
                "old key": {
                    "last_seen": "2026-10-27T00:00:00+00:00",
                    "title": "Old",
                    "source": "https://s.example/feed",
                    "classification": "REVIEW_REQUIRED",
                }
            },
        }
        Path(STATE_FILENAME).write_text(json.dumps(legacy), encoding="utf-8")
        state = load_state_from_artifact()
        assert state["version"] == "1.3"
        assert "old key" in state["seen"]
        assert state["seen"]["old key"]["title"] == "Old"
        assert state["notifications"] == {}


class TestNoSecrets:
    def test_saved_state_contains_no_credentials(self, run_dir):
        state = _fresh_state()
        record_notification(state, "campaign-1", title="Alpha")
        mark_run(state)
        save_state_locally(state)
        raw = Path(STATE_FILENAME).read_text(encoding="utf-8").lower()
        for forbidden in ("token", "chat_id", "private", "secret", "seed"):
            assert forbidden not in raw, forbidden
