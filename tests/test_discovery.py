import pytest
import requests
import requests_mock
from datetime import datetime, timezone

from discovery import (
    Campaign,
    scan_feeds,
    deduplicate_campaigns,
    validate_url,
    FeedError,
)


class TestValidateUrl:
    def test_accepts_https(self):
        assert validate_url("https://example.com/feed.xml") is True

    def test_rejects_http(self):
        assert validate_url("http://example.com/feed.xml") is False

    def test_rejects_empty(self):
        assert validate_url("") is False
        assert validate_url(None) is False

    def test_rejects_too_long(self):
        assert validate_url("https://example.com/" + "x" * 3000) is False

    def test_rejects_dangerous_schemes(self):
        assert validate_url("javascript:alert(1)") is False
        assert validate_url("data:text/html,<h1>hi</h1>") is False


class TestScanFeeds:
    def test_empty_feed_list(self, config):
        campaigns, health, errors = scan_feeds([], config=config)
        assert campaigns == []
        assert health == []
        assert errors == []

    def test_invalid_url_skipped(self, config):
        campaigns, health, errors = scan_feeds(
            ["http://example.com", "https://valid.example/feed.xml"], config=config
        )
        assert any("Invalid feed URL" in e for e in errors) is True

    def test_mock_feed_parsed(self, config):
        xml = (
            '<?xml version="1.0"?>'
            '<rss version="2.0">'
            '<channel>'
            '<title>Test Feed</title>'
            '<item>'
            '<title>Campaign One</title>'
            '<link>https://example.com/c1</link>'
            '<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>'
            '<description>An airdrop campaign for holders.</description>'
            '</item>'
            '</channel>'
            '</rss>'
        )
        with requests_mock.Mocker() as m:
            m.get("https://feeds.example.com/feed.xml", text=xml, status_code=200)
            campaigns, health, errors = scan_feeds(
                ["https://feeds.example.com/feed.xml"], config=config
            )
        assert len(campaigns) >= 1
        assert any(c.source == "https://feeds.example.com/feed.xml" for c in campaigns)
        assert any(h.url == "https://feeds.example.com/feed.xml" for h in health)

    def test_feed_error_does_not_crash(self, config):
        with requests_mock.Mocker() as m:
            m.get("https://broken.example/feed.xml", exc=requests.exceptions.ConnectionError)
            campaigns, health, errors = scan_feeds(
                ["https://broken.example/feed.xml"], config=config
            )
        assert campaigns == []
        assert any("ConnectionError" in e or "Failed" in e for e in errors)

    def test_scam_indicators_flagged(self, config):
        xml = (
            '<?xml version="1.0"?>'
            '<rss version="2.0">'
            '<channel>'
            '<item>'
            '<title>Free Money Airdrop</title>'
            '<link>https://short.link/abc</link>'
            '<description>Send funds to claim your guaranteed profit.</description>'
            '</item>'
            '</channel>'
            '</rss>'
        )
        with requests_mock.Mocker() as m:
            m.get("https://feeds.example.com/flag.xml", text=xml, status_code=200)
            campaigns, health, errors = scan_feeds(
                ["https://feeds.example.com/flag.xml"], config=config
            )
        assert len(campaigns) == 1
        c = campaigns[0]
        assert c.risk_flags
        assert "deposit_required" in c.risk_flags or "guaranteed_profit" in c.risk_flags
        assert c.classification == "HIGH_RISK_INDICATORS"


class TestDeduplication:
    def test_identical_seen_campaign_skipped(self):
        state = {
            "version": "1.1",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "seen": {
                "campaign one\x00https://feeds.example.com/feed.xml\x00https://example.com/c1": {
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "title": "Campaign One",
                    "source": "https://feeds.example.com/feed.xml",
                    "classification": "REVIEW_REQUIRED",
                }
            },
        }
        campaigns = [
            Campaign(
                title="Campaign One",
                project="Proj",
                url="https://example.com/c1",
                source="https://feeds.example.com/feed.xml",
            )
        ]
        new, updated, dupes = deduplicate_campaigns(campaigns, state, stale_days=7)
        assert len(new) == 0
        assert len(dupes) == 1

    def test_new_campaign_kept(self):
        state = {"version": "1.1", "updated_at": "2024-01-01T00:00:00+00:00", "seen": {}}
        campaigns = [
            Campaign(
                title="New Campaign",
                project="Proj",
                url="https://example.com/new",
                source="https://feeds.example.com/feed.xml",
            )
        ]
        new, updated, dupes = deduplicate_campaigns(campaigns, state, stale_days=7)
        assert len(new) == 1
        assert len(dupes) == 0
        assert any("new campaign" in k for k in updated["seen"])

    def test_state_trimmed_when_large(self):
        state = {"version": "1.1", "updated_at": "2024-01-01T00:00:00+00:00", "seen": {}}
        for i in range(250):
            key = f"campaign{i}\x00source{i}\x00url{i}"
            state["seen"][key] = {"last_seen": "2024-01-01T00:00:00+00:00"}
        campaigns = []
        new, updated, dupes = deduplicate_campaigns(campaigns, state, max_entries=200, stale_days=7)
        assert len(updated["seen"]) <= 200
