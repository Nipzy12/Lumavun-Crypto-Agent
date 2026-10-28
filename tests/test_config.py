import os
import pytest
from config_loader import effective_feeds, load_config


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
    def test_env_feeds_take_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AIRDROP_RSS_FEEDS", "https://env.example/feed.xml")
        feeds = effective_feeds()
        assert feeds == ["https://env.example/feed.xml"]

    def test_missing_env_uses_defaults(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AIRDROP_RSS_FEEDS", raising=False)
        feeds = effective_feeds()
        assert feeds == []
