"""V1.3 notification tests: opportunity blocks, daily summary, chunking.

The requirement is specific: every newly discovered opportunity must carry
project, title, type, official source, required actions, reward status,
deadline (when the source gave one), risk indicators and the reason it
passes the zero-capital filter - and the daily summary must go out even
when nothing new was found.
"""

import pytest

from telegram import (
    build_daily_summary,
    build_messages,
    build_opportunity_block,
    build_report_message,
    chunk_message,
    notify,
    send_messages,
)

import requests_mock


def full_campaign(**overrides):
    payload = {
        "campaign_id": "abc123",
        "title": "Meta Chain Airdrop",
        "project": "Meta Chain",
        "url": "https://proof.meta.io/",
        "source": "https://airdropalert.com/feed/rssfeed",
        "announcement_url": "https://airdropalert.com/airdrops/meta-chain/",
        "official_url": "https://meta.io/",
        "opportunity_type": "CONFIRMED_AIRDROP",
        "opportunity_reason": "Menyebut airdrop bersama bukti konfirmasi.",
        "deadline": "2026-12-01",
        "deadline_text": "The claim window closes on 2026-12-01.",
        "tasks": [
            "Follow the official account",
            "Join the discord and submit your wallet",
        ],
        "reward_status": "stated",
        "reward_text": "4.4 billion $META distributed to participants",
        "reward_note": "Jumlah hadiah disebut eksplisit oleh sumber.",
        "zero_capital_ok": True,
        "zero_capital_reason": "Hanya aksi off-chain: follow, join",
        "risk_flags": [],
        "risk_score": 0,
        "risk_band": "low",
        "risk_explanation": ["Tidak ada indikator risiko yang cocok di teks sumber."],
        "classification": "LOW_RISK_INDICATORS",
        "cross_checked": True,
        "cross_check_note": "Disebut oleh 2 sumber berbeda: a.example, b.example",
    }
    payload.update(overrides)
    return payload


HEALTH = [
    {"url": "https://a.example/feed.xml", "reachable": True},
    {"url": "https://b.example/feed.xml", "reachable": True},
]


class TestOpportunityBlock:
    def test_block_contains_every_required_field(self):
        block = build_opportunity_block(full_campaign(), index=1)
        # The project is already the start of the title, so it is not repeated.
        assert block.startswith("1. Meta Chain Airdrop")
        assert "Tipe: CONFIRMED_AIRDROP" in block
        assert "Alasan:" in block
        assert "Sumber resmi: https://meta.io/" in block
        assert "Link: https://proof.meta.io/" in block
        assert "Listing: https://airdropalert.com/airdrops/meta-chain/" in block
        assert "Aksi:" in block
        assert "Follow the official account" in block
        assert "Hadiah: disebut sumber" in block
        assert "4.4 billion $META" in block
        assert "Deadline: 2026-12-01" in block
        assert "claim window closes" in block
        assert "Zero-capital: YA - tanpa setoran/pembelian" in block
        assert "Risiko: 0/100 (low)" in block
        assert "Verifikasi: Disebut oleh 2 sumber" in block

    def test_link_and_official_url_are_shown_separately(self):
        """Referral stripping happens at parse time; here both links must be
        visible so a reader can verify them."""
        block = build_opportunity_block(full_campaign(), index=1)
        assert "Sumber resmi: https://meta.io/" in block
        assert "Link: https://proof.meta.io/" in block

    def test_missing_deadline_says_not_stated(self):
        block = build_opportunity_block(full_campaign(deadline=None, deadline_text=None), index=2)
        assert "Deadline: tidak disebutkan sumber" in block

    def test_missing_tasks_say_not_detailed(self):
        block = build_opportunity_block(full_campaign(tasks=[]), index=1)
        assert "Aksi: tidak dirinci di sumber" in block

    def test_points_program_is_never_sold_as_a_token_reward(self):
        block = build_opportunity_block(
            full_campaign(
                opportunity_type="POINTS_PROGRAM",
                reward_status="points_only",
                reward_text="climb the leaderboard this season",
                reward_note="Hanya poin/leaderboard yang disebut; tidak ada janji token.",
            ),
            index=1,
        )
        assert "Tipe: POINTS_PROGRAM" in block
        assert "Hadiah: hanya poin" in block
        assert "tidak ada janji token" in block

    def test_speculative_reward_is_labelled(self):
        block = build_opportunity_block(
            full_campaign(reward_status="speculative", reward_text="may receive tokens"),
            index=1,
        )
        assert "Hadiah: spekulatif" in block

    def test_blocked_campaign_shows_zero_capital_failure(self):
        block = build_opportunity_block(
            full_campaign(
                zero_capital_ok=False,
                zero_capital_reason="Menyebut biaya atau setoran: minimum deposit",
                risk_score=75,
                risk_band="high",
                risk_explanation=["Required deposit or upfront payment (+30)"],
                classification="HIGH_RISK_INDICATORS",
            ),
            index=1,
        )
        assert "Zero-capital: TIDAK" in block
        assert "minimum deposit" in block
        assert "Risiko: 75/100 (high)" in block

    def test_unknown_gas_costs_are_reported_as_unknown(self):
        block = build_opportunity_block(
            full_campaign(
                zero_capital_ok=None,
                zero_capital_reason="Ada aksi on-chain (bridge) tetapi biaya gas tidak disebutkan.",
                unknown_costs=["Biaya gas transaksi on-chain tidak disebutkan sumber"],
            ),
            index=1,
        )
        assert "Zero-capital: BELUM JELAS" in block


class TestDailySummary:
    def test_summary_is_sent_even_with_no_new_campaigns(self):
        summary = build_daily_summary(
            scan_time="2026-10-28T00:00:00+00:00",
            wallet_summary={"sol_balance": 0.0, "spl_token_count": 0, "spl_status": "ok"},
            campaigns=[],
            source_health=HEALTH,
            errors=[],
            total_found=0,
            duplicates_skipped=7,
        )
        assert "ringkasan harian" in summary
        assert "Saldo SOL: 0.000000 SOL" in summary
        assert "Sumber: 2/2" in summary
        assert "duplikat dilewati: 7" in summary
        assert "Tidak ada peluang baru" in summary
        assert "Tidak ada klaim otomatis" in summary

    def test_summary_counts_types_and_zero_capital(self):
        campaigns = [
            full_campaign(),
            full_campaign(opportunity_type="POINTS_PROGRAM", zero_capital_ok=False),
            full_campaign(zero_capital_ok=None),
        ]
        summary = build_daily_summary(
            scan_time="2026-10-28T00:00:00+00:00",
            wallet_summary={"sol_balance": 1.5, "spl_token_count": 2, "spl_status": "ok"},
            campaigns=campaigns,
            source_health=HEALTH,
            errors=[],
            total_found=9,
            duplicates_skipped=4,
        )
        # Two CONFIRMED_AIRDROP (payload defaults) plus one POINTS_PROGRAM.
        assert "CONFIRMED_AIRDROP 2" in summary
        assert "POINTS_PROGRAM 1" in summary
        assert "Zero-capital: 1 lolos | 1 butuh biaya | 1 belum jelas" in summary
        assert "Kampanye ditemukan: 9 | baru: 3 | duplikat dilewati: 4" in summary

    def test_failed_sources_are_listed(self):
        summary = build_daily_summary(
            scan_time="now",
            wallet_summary={"sol_balance": None, "spl_token_count": None, "spl_status": "unavailable"},
            campaigns=[],
            source_health=[
                {"url": "https://ok.example/feed", "reachable": True},
                {"url": "https://dead.example/feed", "reachable": False, "error": "HTTP 500"},
            ],
            errors=["Source error (dead): HTTP 500"],
        )
        assert "Sumber: 1/2" in summary
        assert "Sumber gagal:" in summary
        assert "https://dead.example/feed" in summary
        assert "tidak tersedia (RPC gagal)" in summary

    def test_wallet_failure_is_not_reported_as_empty(self):
        summary = build_daily_summary(
            scan_time="now",
            wallet_summary={"sol_balance": None, "spl_token_count": None, "spl_status": "unavailable"},
            campaigns=[],
            source_health=[],
            errors=[],
        )
        assert "Saldo SOL: tidak tersedia" in summary
        assert "RPC gagal" in summary

    def test_empty_wallet_reports_zero_not_unavailable(self):
        summary = build_daily_summary(
            scan_time="now",
            wallet_summary={"sol_balance": 0.0, "spl_token_count": 0, "spl_status": "ok"},
            campaigns=[],
            source_health=[],
            errors=[],
        )
        assert "Saldo SOL: 0.000000 SOL" in summary
        assert "saldo kosong, RPC berhasil" in summary


class TestChunking:
    def test_short_message_is_not_split(self):
        assert chunk_message("hello") == ["hello"]

    def test_long_message_stays_under_the_byte_budget(self):
        blocks = "\n\n".join(f"Block {i}: " + "x" * 900 for i in range(12))
        chunks = chunk_message(blocks, max_bytes=1000)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.encode("utf-8")) <= 1000

    def test_chunking_preserves_all_content(self):
        blocks = "\n\n".join(f"Block {i}: " + "y" * 400 for i in range(8))
        chunks = chunk_message(blocks, max_bytes=900)
        for i in range(8):
            joined = "\n\n".join(chunks)
            assert f"Block {i}:" in joined

    def test_messages_start_with_the_summary(self):
        messages = build_messages(
            scan_time="now",
            wallet_summary={"sol_balance": 1.0, "spl_token_count": 0, "spl_status": "ok"},
            campaigns=[full_campaign() for _ in range(7)],
            source_health=HEALTH,
            errors=[],
        )
        assert messages[0].startswith("LUMAVUN Crypto Agent V1.3 - ringkasan harian")
        assert all(len(m.encode("utf-8")) <= 4000 for m in messages)
        # Opportunity blocks are grouped, three per message.
        block_messages = messages[1:]
        assert len(block_messages) == 3

    def test_message_lists_the_highest_index_accurately(self):
        messages = build_messages(
            scan_time="now",
            wallet_summary={"sol_balance": 1.0, "spl_token_count": 0, "spl_status": "ok"},
            campaigns=[full_campaign() for _ in range(4)],
            source_health=HEALTH,
            errors=[],
        )
        joined = "\n".join(messages)
        assert "1. Meta Chain" in joined
        assert "4. Meta Chain" in joined

    def test_overflow_is_announced(self):
        messages = build_messages(
            scan_time="now",
            wallet_summary={"sol_balance": 1.0, "spl_token_count": 0, "spl_status": "ok"},
            campaigns=[full_campaign() for _ in range(9)],
            source_health=HEALTH,
            errors=[],
            max_opportunities=4,
        )
        assert any("peluang lain ada di laporan" in m for m in messages)


class TestSendMessages:
    def test_all_messages_delivered(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bot123456:TESTTOKEN/sendMessage",
                json={"ok": True, "result": {"message_id": 1}},
                status_code=200,
            )
            result = send_messages("123456:TESTTOKEN", "-100", ["one", "two"])
        assert result["sent"] is True
        assert result["sent_count"] == 2
        assert result["messages"] == 2
        assert result["error"] is None

    def test_partial_failure_is_reported_but_others_still_send(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bot123456:TESTTOKEN/sendMessage",
                [
                    {"json": {"ok": True, "result": {"message_id": 1}}, "status_code": 200},
                    {"json": {"ok": False, "description": "message too long"}, "status_code": 400},
                    {"json": {"ok": True, "result": {"message_id": 3}}, "status_code": 200},
                ],
            )
            result = send_messages("123456:TESTTOKEN", "-100", ["one", "two", "three"])
        assert result["sent"] is False
        assert result["sent_count"] == 2
        assert "message too long" in result["error"]

    def test_empty_list_is_reported_not_silently_ok(self):
        result = send_messages("123456:TESTTOKEN", "-100", [])
        assert result["sent"] is False
        assert result["error"] == "Nothing to send"

    def test_unconfigured_telegram_is_an_error_not_a_crash(self):
        result = send_messages("", "", ["one"])
        assert result["sent"] is False
        assert "not configured" in result["error"]

    def test_notify_never_returns_the_token(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://api.telegram.org/bot123456:TESTTOKEN/sendMessage",
                json={"ok": True, "result": {"message_id": 1}},
                status_code=200,
            )
            result = notify("123456:TESTTOKEN", "-100", "hello")
        assert result["sent"] is True
        assert "TESTTOKEN" not in str(result.get("raw", ""))
        assert "TESTTOKEN" not in str(result.get("error", ""))


class TestLegacyMessage:
    def test_legacy_builder_still_works(self):
        msg = build_report_message(
            scan_time="2026-10-28T00:00:00+00:00",
            wallet_summary={"sol_balance": 0.5, "spl_token_count": 1},
            campaigns=[full_campaign()],
            source_health=HEALTH,
            errors=[],
        )
        assert "LUMAVUN Crypto Agent V1.3" in msg
        assert "Meta Chain Airdrop" in msg
        assert "CONFIRMED_AIRDROP" in msg
        assert "Sumber feed: 2/2" in msg
