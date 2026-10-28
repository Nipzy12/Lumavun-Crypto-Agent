import pytest

from config_loader import effective_feeds, load_config

# The three sources carried in config/vetted_sources.yaml after HTTP
# verification during V1.2. Tests assert the file ships exactly these so a
# dead or invented URL cannot be reintroduced unnoticed.
VERIFIED_FEEDS = [
    "https://airdropalert.com/feed/rssfeed",
    "https://airdrops.io/feed/",
    "https://decrypt.co/feed",
]


class TestLoadConfig:
    def test_loads_existing_config(self):
        config = load_config()
        assert isinstance(config, dict)
        assert "risk_rules" in config
        assert "classification" in config

    def test_defaults_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config_loader.CONFIG_PATH", tmp_path / "missing.yaml")
        config = load_config()
        assert config["default_feeds"] == []


class TestEffectiveFeeds:
    def test_env_feeds_take_precedence(self, monkeypatch):
        monkeypatch.setenv("AIRDROP_RSS_FEEDS", "https://env.example/feed.xml")
        assert effective_feeds() == ["https://env.example/feed.xml"]

    def test_env_splits_and_trims_multiple_feeds(self, monkeypatch):
        monkeypatch.setenv(
            "AIRDROP_RSS_FEEDS", " https://a.example/rss , https://b.example/rss "
        )
        assert effective_feeds() == [
            "https://a.example/rss",
            "https://b.example/rss",
        ]

    def test_missing_env_uses_verified_defaults(self, monkeypatch):
        monkeypatch.delenv("AIRDROP_RSS_FEEDS", raising=False)
        assert effective_feeds() == VERIFIED_FEEDS

    def test_blank_env_uses_verified_defaults(self, monkeypatch):
        monkeypatch.setenv("AIRDROP_RSS_FEEDS", "   ")
        assert effective_feeds() == VERIFIED_FEEDS


class TestVerifiedSourcesConfig:
    @pytest.fixture
    def config(self):
        return load_config()

    def test_at_least_three_default_feeds(self, config):
        assert len(config["default_feeds"]) >= 3

    def test_defaults_match_verified_list_exactly(self, config):
        assert config["default_feeds"] == VERIFIED_FEEDS

    def test_all_defaults_are_https(self, config):
        for url in config["default_feeds"]:
            assert url.startswith("https://"), url

    def test_no_rejected_source_sneaks_into_defaults(self, config):
        rejected = {entry["url"] for entry in config.get("rejected_sources", [])}
        for url in config["default_feeds"]:
            assert url not in rejected, f"rejected URL still configured: {url}"

    def test_verification_record_covers_every_default(self, config):
        verified = {entry["url"] for entry in config.get("verified_sources", [])}
        for url in config["default_feeds"]:
            assert url in verified, f"default feed missing verification record: {url}"

    def test_verification_records_report_http_200(self, config):
        for entry in config.get("verified_sources", []):
            assert entry["http_status"] == 200, entry["url"]
            assert entry.get("feed_format"), entry["url"]
            assert entry.get("focus"), entry["url"]

    def test_known_sources_helper_exposes_feeds(self):
        from config_loader import known_sources

        info = known_sources()
        assert info["default_feeds"] == VERIFIED_FEEDS
        assert info["version"] == "1.2"
