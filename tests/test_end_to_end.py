"""End-to-end test: the real ``main()`` against mocked HTTP.

This is the closest thing to a CI run without leaving the test process:

* every configured source is answered by requests_mock (robots.txt included),
* the wallet RPC answers health/balance/token queries,
* Telegram accepts the messages,
* the second run starts on a *different* working directory with the state
  file copied in, exactly like a fresh GitHub Actions runner restoring the
  ``dedup_state`` artifact.

Assertions cover discovery, classification, the zero-capital filter, report
artifacts, Telegram delivery, and cross-run deduplication.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
import requests_mock

import main as main_module
from sources import reset_network_caches

ROBOTS = "User-agent: *\nDisallow:\n"

VALID_ADDRESS = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"

FEED_BODIES = {
    # A dedicated airdrop listing feed: one hard-confirmed campaign.
    "https://airdropalert.com/feed/rssfeed": """<?xml version="1.0"?>
<rss version="2.0"><channel><title>AirdropAlert</title>
<item>
  <title>Meta Chain Airdrop</title>
  <link>https://airdropalert.com/airdrops/meta-chain/</link>
  <pubDate>Wed, 28 Oct 2026 10:00:00 +0000</pubDate>    <description>The snapshot is confirmed and the claim window closes on 2026-12-01.
  Follow the official account, join the discord and submit your wallet to qualify.</description>
</item>
<item>
  <title>Flop Network Genesis</title>
  <link>https://airdropalert.com/airdrops/flop/</link>
  <pubDate>Tue, 27 Oct 2026 10:00:00 +0000</pubDate>
  <description>The genesis testnet reward is distributed to node operators.</description>
</item>
</channel></rss>""",

    # A points/testnet listing feed.
    "https://airdrops.io/feed/": """<?xml version="1.0"?>
<rss version="2.0"><channel><title>airdrops.io</title>
<item>
  <title>Quip Network Points Guide</title>
  <link>https://airdrops.io/blog/quip-network-node/</link>
  <pubDate>Fri, 02 Oct 2026 16:59:32 +0000</pubDate>
  <description>Climb the leaderboard this season and collect points for the quest.</description>
</item>
<item>
  <title>Bridge Rewards</title>
  <link>https://airdrops.io/blog/bridge-rewards/</link>
  <pubDate>Thu, 01 Oct 2026 16:59:32 +0000</pubDate>
  <description>Bridge your assets to the testnet to qualify for the reward.</description>
</item>
</channel></rss>""",

    # A third-party listing of the same campaign: proves cross-checking.
    "https://decrypt.co/feed": """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Decrypt</title>
<item>
  <title>Meta Chain Airdrop</title>
  <link>https://decrypt.co/articles/meta-chain-airdrop</link>
  <pubDate>Wed, 28 Oct 2026 11:00:00 +0000</pubDate>
  <description>The snapshot is confirmed and the claim window closes on 2026-12-01.</description>
</item>
<item>
  <title>Market Roundup</title>
  <link>https://decrypt.co/articles/roundup</link>
  <pubDate>Wed, 28 Oct 2026 12:00:00 +0000</pubDate>
  <description>Prices moved on the day as traders reacted to policy.</description>
</item>
</channel></rss>""",

    "https://blog.ethereum.org/feed.xml": """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Ethereum Blog</title>
<item>
  <title>Glamsterdam Testnet Announcement</title>
  <link>https://blog.ethereum.org/glamsterdam-testnet</link>
  <pubDate>Thu, 22 Oct 2026 00:00:00 GMT</pubDate>
  <description>The testnet upgrade is announced for node operators and validators.</description>
</item>
</channel></rss>""",

    "https://blog.arbitrum.io/feed": """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Arbitrum Blog</title>
<item>
  <title>Stablecoin Growth Incentive</title>
  <link>https://blog.arbitrum.io/incentive</link>
  <pubDate>Thu, 15 Oct 2026 13:43:20 GMT</pubDate>
  <description>A new incentive programme with rewards for early participants.</description>
</item>
</channel></rss>""",
}

# The JSON (WordPress REST) source.
WP_API = "https://airdropalert.com/wp-json/wp/v2/posts?per_page=10&_fields=id,date_gmt,link,title,excerpt"
WP_BODY = json.dumps(
    [
        {
            "id": 329103,
            "date_gmt": "2026-10-28T12:37:35",
            "link": "https://airdropalert.com/blogs/testnet-guide/",
            "title": {"rendered": "Testnet airdrop guide"},
            "excerpt": {"rendered": "How to join the testnet quest for points."},
        },
        {
            "id": 1,
            "date_gmt": "2026-10-28T05:24:00",
            "link": "https://airdropalert.com/blogs/market/",
            "title": {"rendered": "How to buy WLFI"},
            "excerpt": {"rendered": "A primer for newcomers to exchanges."},
        },
    ]
)


def _feed_origin(url):
    from urllib.parse import urlparse

    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Neutral environment: no operator overrides, isolated working dir."""
    for name in (
        "AIRDROP_RSS_FEEDS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "LUMAVUN_STATE_SOURCE",
        "PHANTOM_PUBLIC_ADDRESS",
        "SOLANA_RPC_URL",
        "SOLANA_RPC_FALLBACK_URLS",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_network_caches()
    monkeypatch.chdir(tmp_path)
    yield
    reset_network_caches()


def _register_all(m):
    for url, body in FEED_BODIES.items():
        m.get(_feed_origin(url) + "/robots.txt", text=ROBOTS, status_code=200)
        m.get(url, text=body, status_code=200)
    m.get("https://airdropalert.com/robots.txt", text=ROBOTS, status_code=200)
    m.get(WP_API, text=WP_BODY, status_code=200)

    # Wallet RPC: health, balance (1.5 SOL), token accounts (1 account).
    m.post(
        "https://rpc.e2e.example",
        [
            {"json": {"jsonrpc": "2.0", "result": {"epoch": "7"}, "id": 1}, "status_code": 200},
            {"json": {"jsonrpc": "2.0", "result": {"value": 1500000000}, "id": 1}, "status_code": 200},
            {
                "json": {
                    "jsonrpc": "2.0",
                    "result": {
                        "value": [
                            {
                                "account": {
                                    "data": {
                                        "parsed": {
                                            "info": {
                                                "mint": "So11111111111111111111111111111111111111112",
                                                "tokenAmount": {
                                                    "amount": "2500000000",
                                                    "decimals": 9,
                                                    "uiAmount": 2.5,
                                                    "uiAmountString": "2.5",
                                                },
                                            }
                                        },
                                        "program": "spl-token",
                                    },
                                    "space": 165,
                                }
                            }
                        ]
                    },
                    "id": 1,
                },
                "status_code": 200,
            },
        ],
    )

    m.post(
        "https://api.telegram.org/bot123456:TESTTOKEN/sendMessage",
        json={"ok": True, "result": {"message_id": 1}},
        status_code=200,
    )


def _run_main(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("PHANTOM_PUBLIC_ADDRESS", VALID_ADDRESS)
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.e2e.example")
    monkeypatch.setenv("LUMAVUN_STATE_SOURCE", "run:previous")
    return main_module.main()


def _report():
    return json.loads(Path("latest_report.json").read_text(encoding="utf-8"))


def test_first_run_discovers_classifies_and_notifies(monkeypatch):
    with requests_mock.Mocker() as m:
        _register_all(m)
        assert _run_main(monkeypatch) == 0

    report = _report()

    # Artifacts exist in both formats.
    assert Path("latest_report.md").exists()
    assert Path("dedup_state.json").exists()

    # Sources: all six registry entries were read.
    assert report["source_summary"]["total"] == 6
    assert report["source_summary"]["reachable"] == 6
    assert report["source_summary"]["official_sources"] == 2

    # Discovery + classification.
    assert report["campaign_count"] >= 5
    types = report["opportunity_summary"]
    assert types["CONFIRMED_AIRDROP"] >= 1
    assert types["POINTS_PROGRAM"] >= 1
    assert types["TESTNET_POTENTIAL"] >= 1
    assert set(types) == {
        "CONFIRMED_AIRDROP",
        "POINTS_PROGRAM",
        "TESTNET_POTENTIAL",
        "FREE_QUEST",
        "UNVERIFIED",
    }

    # Cross-checking across two hosts marked the shared campaign.
    shared = [c for c in report["campaigns"] if c["title"] == "Meta Chain Airdrop"]
    assert len(shared) >= 2
    assert any(c["cross_checked"] for c in shared)

    # Zero-capital filtering happened and is reported per campaign.
    zero = report["zero_capital_summary"]
    assert zero["passes"] >= 3
    assert report["campaigns"][0]["zero_capital_ok"] in (True, False, None)

    # Extraction: deadline, tasks, reward status are present for the campaign
    # that states them.
    confirmed = [
        c for c in report["campaigns"] if c["opportunity_type"] == "CONFIRMED_AIRDROP"
    ]
    assert confirmed
    assert all(c["reward_status"] in ("stated", "points_only", "speculative", "unspecified") for c in confirmed)
    assert all(c["risk_explanation"] for c in confirmed)

    # The campaign that states a dated claim window carries it through, and
    # the listing that quotes actions has them extracted.
    meta = [c for c in confirmed if c["title"] == "Meta Chain Airdrop"]
    assert len(meta) == 2
    assert all(c["deadline"] == "2026-12-01" for c in meta)
    assert any(c["tasks"] for c in meta), "stated tasks must be extracted"

    # Wallet: SOL balance read, SPL status distinguished from failure.
    wallet = report["wallet"]
    assert wallet["sol_balance"] == pytest.approx(1.5)
    assert wallet["spl_status"] == "ok"
    assert wallet["spl_token_count"] == 1
    assert wallet["spl_tokens"][0]["ui_amount"] == pytest.approx(2.5)

    # Telegram: summary plus per-opportunity blocks were delivered.
    assert report["telegram"]["sent"] is True
    assert report["telegram"]["messages"] >= 2
    assert report["telegram"]["sent_count"] == report["telegram"]["messages"]

    # State carries notification history for the next run.
    state = json.loads(Path("dedup_state.json").read_text(encoding="utf-8"))
    assert state["version"] == "1.3"
    assert state["seen"]
    assert state["notifications"]
    assert state["runs"] == 1
    assert report["state_source"] == "run:previous"

    # The dangerous words never appear in the delivered Telegram payload.
    sent = [c.text for c in m.request_history if "sendMessage" in str(c.url)]
    assert sent
    joined = "\n".join(sent)
    assert "LUMAVUN Crypto Agent V1.3" in joined
    assert "CONFIRMED_AIRDROP" in joined
    assert "Zero-capital" in joined


def test_second_run_on_a_fresh_runner_sends_no_duplicates(monkeypatch, tmp_path):
    # ---- Run 1 -------------------------------------------------------
    with requests_mock.Mocker() as m:
        _register_all(m)
        assert _run_main(monkeypatch) == 0
    first = _report()
    state_copy = tmp_path / "restored_state.json"
    shutil.copy(tmp_path / "dedup_state.json", state_copy)
    first_ids = {c["campaign_id"] for c in first["campaigns"]}

    # ---- Run 2 in a different working directory, as a fresh runner ----
    runner2 = tmp_path / "runner2"
    runner2.mkdir()
    shutil.copy(state_copy, runner2 / "dedup_state.json")
    monkeypatch.chdir(runner2)

    with requests_mock.Mocker() as m:
        _register_all(m)
        assert _run_main(monkeypatch) == 0

    second = _report()
    assert second["campaign_count"] == first["campaign_count"], "same feed content"
    assert second["duplicates_skipped"] == first["campaign_count"]
    assert {c["campaign_id"] for c in second["campaigns"]} == first_ids

    # Only the daily summary goes out; no opportunity blocks are repeated.
    assert second["telegram"]["messages"] == 1
    assert second["telegram"]["sent"] is True
    assert second["telegram"]["sent_count"] == 1

    # Notification history survived and did not grow, and the run counter
    # reflects both runs.
    state = json.loads((runner2 / "dedup_state.json").read_text(encoding="utf-8"))
    assert state["runs"] == 2
    assert set(state["notifications"]) == {
        c["campaign_id"] for c in first["campaigns"]
    }


def test_report_markdown_is_readable_and_labels_heuristics(monkeypatch):
    with requests_mock.Mocker() as m:
        _register_all(m)
        assert _run_main(monkeypatch) == 0

    text = Path("latest_report.md").read_text(encoding="utf-8")
    assert "# LUMAVUN Crypto Agent V1.3" in text
    assert "## Kesehatan sumber" in text
    assert "## Ringkasan tipe peluang" in text
    assert "## Filter zero-capital" in text
    assert "Zero-capital" in text
    assert "Status hadiah" in text
    assert "menjamin keamanan" in text
    assert "1.500000 SOL" in text
    # Rejection of fabricated guarantees.
    assert "Tidak ada klaim otomatis" in text or "baca-only" in text


def test_a_dead_source_does_not_stop_the_run(monkeypatch):
    with requests_mock.Mocker() as m:
        _register_all(m)
        # Kill one source after registration.
        m.get("https://airdrops.io/feed/", status_code=500)
        assert _run_main(monkeypatch) == 0

    report = _report()
    failed = [h for h in report["source_health"] if not h["reachable"]]
    assert len(failed) == 1
    assert failed[0]["error"] == "HTTP 500"
    assert report["source_summary"]["reachable"] == 5
    assert report["errors"]
    assert report["campaign_count"] >= 1, "other sources still produced campaigns"
