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
        # V1.3 keys state by a stable campaign id, not by raw title text, so
        # assert on the stored record instead of the key.
        assert any(
            record.get("title") == "New Campaign" for record in updated["seen"].values()
        )
        assert len(updated["seen"]) == 1

    def test_state_trimmed_when_large(self):
        state = {"version": "1.1", "updated_at": "2024-01-01T00:00:00+00:00", "seen": {}}
        for i in range(250):
            key = f"campaign{i}\x00source{i}\x00url{i}"
            state["seen"][key] = {"last_seen": "2024-01-01T00:00:00+00:00"}
        campaigns = []
        new, updated, dupes = deduplicate_campaigns(campaigns, state, max_entries=200, stale_days=7)
        assert len(updated["seen"]) <= 200

    def test_stable_id_is_used_as_the_state_key(self):
        """V1.3: state keys are stable campaign ids, not raw title strings."""
        campaigns = [
            Campaign(
                title="Campaign One",
                project="Proj",
                url="https://example.com/c1",
                source="https://feeds.example.com/feed.xml",
            )
        ]
        state = {"version": "1.3", "updated_at": "2024-01-01T00:00:00+00:00", "seen": {}}
        new, updated, dupes = deduplicate_campaigns(campaigns, state)
        key = next(iter(updated["seen"]))
        assert key == campaigns[0].campaign_id
        assert "\x00" not in key
        assert len(key) == 24

    def test_same_campaign_from_two_runs_gets_the_same_id(self):
        def build():
            return Campaign(
                title="Campaign One",
                project="Proj",
                url="https://example.com/c1",
                source="https://feeds.example.com/feed.xml",
            )

        assert build().campaign_id == build().campaign_id

    def test_different_campaigns_get_different_ids(self):
        a = Campaign(title="A", project="A", url="https://x/1", source="https://f/rss")
        b = Campaign(title="B", project="B", url="https://x/2", source="https://f/rss")
        assert a.campaign_id != b.campaign_id


class TestMultiSourceScan:
    """V1.3: several sources, keyword gating, and cross-source evidence."""

    IMPORTS = [
        ("https://a.example/feed.xml", "Alpha Airdrop", "A confirmed campaign with a reward."),
        ("https://b.example/feed.xml", "Beta Testnet", "A testnet campaign announcement."),
    ]

    @pytest.fixture
    def two_sources(self, config):
        from sources import SourceSpec

        return [
            SourceSpec(id="a", url=self.IMPORTS[0][0], label="A", category="airdrop_listing"),
            SourceSpec(id="b", url=self.IMPORTS[1][0], label="B", category="news"),
        ]

    @staticmethod
    def _xml(title, summary):
        return (
            '<?xml version="1.0"?>'
            '<rss version="2.0"><channel><title>t</title><item>'
            f"<title>{title}</title><link>https://x.example/{title}</link>"
            f"<description>{summary}</description>"
            "</item></channel></rss>"
        )

    def test_both_sources_are_read(self, two_sources, config):
        from discovery import scan_sources

        with requests_mock.Mocker() as m:
            for url, _, _ in self.IMPORTS:
                origin = f"{url.split('/')[0]}//{url.split('/')[2]}"
                m.get(origin + "/robots.txt", text="User-agent: *\n", status_code=200)
            m.get(self.IMPORTS[0][0], text=self._xml(*self.IMPORTS[0][1:]), status_code=200)
            m.get(self.IMPORTS[1][0], text=self._xml(*self.IMPORTS[1][1:]), status_code=200)
            campaigns, health, errors = scan_sources(two_sources, config=config, min_interval=0)

        assert errors == []
        assert len(health) == 2
        assert all(h.reachable for h in health)
        assert {c.source_id for c in campaigns} == {"a", "b"}
        assert all(c.campaign_id for c in campaigns)

    def test_entry_without_campaign_keywords_is_dropped(self, two_sources, config):
        from discovery import scan_sources
        from sources import SourceSpec

        only = [SourceSpec(id="a", url="https://a.example/feed.xml")]
        xml = self._xml("Market Roundup", "Prices moved on the day as policy changed.")
        with requests_mock.Mocker() as m:
            m.get("https://a.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://a.example/feed.xml", text=xml, status_code=200)
            campaigns, health, errors = scan_sources(only, config=config, min_interval=0)

        assert campaigns == []
        assert health[0].reachable is True
        assert health[0].entries_analyzed == 1
        assert health[0].campaigns_found == 0

    def test_health_carries_http_metadata(self, two_sources, config):
        from discovery import scan_sources

        with requests_mock.Mocker() as m:
            m.get("https://a.example/robots.txt", text="User-agent: *\nDisallow: /private/\n", status_code=200)
            m.get("https://b.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get(self.IMPORTS[0][0], text=self._xml(*self.IMPORTS[0][1:]), status_code=200)
            m.get(self.IMPORTS[1][0], status_code=503)
            _, health, errors = scan_sources(two_sources, config=config, min_interval=0)

        by_url = {h.url: h for h in health}
        ok = by_url["https://a.example/feed.xml"]
        assert ok.http_status == 200
        assert ok.category == "airdrop_listing"
        assert isinstance(ok.content_type, str)
        assert ok.robots_blocked is False
        bad = by_url["https://b.example/feed.xml"]
        assert bad.reachable is False
        assert bad.error == "HTTP 503"
        assert any("b" in e for e in errors)

    def test_cross_source_listing_is_marked_verified(self, config):
        """The same project reported by two hosts is cross-checked."""
        from discovery import scan_sources
        from sources import SourceSpec

        specs = [
            SourceSpec(id="a", url="https://a.example/feed.xml", category="airdrop_listing"),
            SourceSpec(id="b", url="https://b.example/feed.xml", category="official_project", official=True),
        ]
        with requests_mock.Mocker() as m:
            m.get("https://a.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://b.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get(specs[0].url, text=self._xml("Alpha Airdrop", "Confirmed airdrop campaign."), status_code=200)
            m.get(specs[1].url, text=self._xml("Alpha Airdrop", "Official airdrop announcement."), status_code=200)
            campaigns, _, _ = scan_sources(specs, config=config, min_interval=0)

        assert len(campaigns) == 2
        assert all(c.cross_checked for c in campaigns)
        assert all("resmi" in c.cross_check_note for c in campaigns)

    def test_findings_are_ordered_by_zero_capital_then_risk(self, config):
        from discovery import Campaign, _sort_key

        cheap = Campaign(title="Cheap", project="Cheap", url="https://x/1", source="https://f/rss")
        cheap.zero_capital_ok = True
        cheap.risk_score = 0
        expensive = Campaign(title="Paid", project="Paid", url="https://x/2", source="https://f/rss")
        expensive.zero_capital_ok = False
        expensive.risk_score = 0
        risky = Campaign(title="Risky", project="Risky", url="https://x/3", source="https://f/rss")
        risky.zero_capital_ok = True
        risky.risk_score = 75

        ordered = sorted([risky, expensive, cheap], key=_sort_key)
        assert [c.title for c in ordered] == ["Cheap", "Risky", "Paid"]
