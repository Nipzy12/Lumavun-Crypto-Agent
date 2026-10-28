import pytest
import requests
import requests_mock
from telegram import (
    notify,
    build_report_message,
    should_notify_campaign,
    campaign_identity_hash,
)


class TestNotify:
    def test_missing_config_returns_error(self):
        result = notify("", "123", "hello")
        assert result["sent"] is False
        assert "not configured" in result["error"].lower()

    def test_success_sets_sent_true(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bot123456:ABC-DEF/sendMessage",
                json={"ok": True, "result": {"message_id": 1}},
                status_code=200,
            )
            result = notify("123456:ABC-DEF", "123", "Hello")
        assert result["sent"] is True
        assert result["error"] is None

    def test_telegram_failure_does_not_raise(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bot***sendMessage",
                exc=requests.exceptions.HTTPError,
            )
            result = notify("123456:ABC-DEF", "123", "Hello")
        assert result["sent"] is False
        assert result["error"] is not None

    def test_message_trimmed_when_too_long(self):
        long_text = "x" * 5000
        result = notify("123456:ABC-DEF", "123", long_text)
        assert result["sent"] is False  # mocked, so no real send
        assert result["bytes"] <= 4000


class TestMessageBuild:
    def test_empty_campaigns(self):
        msg = build_report_message(
            scan_time="2024-01-01T00:00:00+00:00",
            wallet_summary={"sol_balance": None, "spl_token_count": 0},
            campaigns=[],
            source_health=[{"reachable": False, "url": "https://bad.example"}],
            errors=[],
        )
        assert "Tidak tersedia" in msg or "tidak tersedia" in msg
        assert "Saldo SOL" in msg

    def test_campaigns_included(self):
        msg = build_report_message(
            scan_time="2024-01-01T00:00:00+00:00",
            wallet_summary={"sol_balance": 0.5, "spl_token_count": 2},
            campaigns=[
                {
                    "title": "Airdrop X",
                    "url": "https://example.com/x",
                    "source": "https://feeds.example/feed.xml",
                    "risk_flags": ["deposit_required"],
                    "classification": "HIGH_RISK_INDICATORS",
                    "is_confirmed": False,
                    "is_speculation": False,
                }
            ],
            source_health=[{"reachable": True, "url": "https://feeds.example/feed.xml"}],
            errors=["Feed error (https://other.example): timeout"],
        )
        assert "Airdrop X" in msg
        assert "HIGH_RISK_INDICATORS" in msg
        assert "deposit_required" in msg


class TestMessageBuildAcceptsDataclasses:
    """Regression: main.py passes SourceHealth dataclasses, not dicts.

    build_report_message previously called .get() on them and crashed the
    whole run after a successful scan.
    """

    def test_source_health_dataclasses_are_accepted(self):
        from discovery import SourceHealth

        msg = build_report_message(
            scan_time="2026-01-01T00:00:00+00:00",
            wallet_summary={"sol_balance": 0.0, "spl_token_count": 0},
            campaigns=[],
            source_health=[
                SourceHealth(url="https://a.example/rss", reachable=True, entries_analyzed=5),
                SourceHealth(url="https://b.example/rss", reachable=False, error="timeout"),
            ],
            errors=[],
        )
        assert "1/2" in msg
        assert "Saldo SOL: 0.000000 SOL" in msg

    def test_zero_balance_renders_as_zero_sol(self):
        msg = build_report_message(
            scan_time="2026-01-01T00:00:00+00:00",
            wallet_summary={"sol_balance": 0.0, "spl_token_count": 0},
            campaigns=[],
            source_health=[],
            errors=[],
        )
        assert "0.000000 SOL" in msg
        assert "tidak tersedia" not in msg.split("Jumlah aset")[0]


class TestDuplicatePrevention:
    def test_identical_campaign_skipped(self):
        prev = [
            {"title": "Airdrop X", "source": "https://f.example/feed.xml", "url": "https://example.com/x"}
        ]
        curr = [
            {"title": "Airdrop X", "source": "https://f.example/feed.xml", "url": "https://example.com/x"}
        ]
        assert should_notify_campaign(curr[0], [campaign_identity_hash(p) for p in prev]) is False

    def test_different_campaign_sent(self):
        prev = [
            {"title": "Airdrop X", "source": "https://f.example/feed.xml", "url": "https://example.com/x"}
        ]
        curr = [
            {"title": "Airdrop Y", "source": "https://f.example/feed.xml", "url": "https://example.com/y"}
        ]
        assert should_notify_campaign(curr[0], [campaign_identity_hash(p) for p in prev]) is True
