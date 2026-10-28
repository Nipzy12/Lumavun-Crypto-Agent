import json
from pathlib import Path
from reporter import build_report, write_json_report, write_markdown_report


def test_json_report_writes_file(tmp_path):
    report = build_report(
        scan_time="2024-01-01T00:00:00+00:00",
        campaigns=[],
        source_health=[],
        wallet={"sol_balance": None, "spl_token_count": 0},
        errors=[],
    )
    path = write_json_report(report, path=tmp_path / "report.json")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["wallet"]["sol_balance"] is None


def test_markdown_report_writes_file(tmp_path):
    report = build_report(
        scan_time="2024-01-01T00:00:00+00:00",
        campaigns=[],
        source_health=[],
        wallet={"sol_balance": 0.25, "spl_token_count": 3},
        errors=["Sample error"],
    )
    path = write_markdown_report(report, path=tmp_path / "report.md")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Saldo SOL" in text
    assert "0.250000" in text
    assert "Sample error" in text


def _sample_campaign():
    from discovery import Campaign

    return Campaign(
        title="Meta Chain Airdrop",
        project="Meta Chain",
        url="https://proof.meta.io/",
        source="https://airdropalert.com/feed/rssfeed",
        published="Wed, 28 Oct 2026 10:00:00 +0000",
        requirements=["Follow the account"],
        raw_summary="The claim window closes on 2026-12-01 for participants.",
        source_id="airdropalert-rss",
        source_label="AirdropAlert campaign feed",
        source_category="airdrop_listing",
        official_url="https://meta.io/",
        announcement_url="https://airdropalert.com/airdrops/meta-chain/",
        opportunity_type="CONFIRMED_AIRDROP",
        opportunity_reason="Menyebut airdrop bersama bukti konfirmasi.",
        deadline="2026-12-01",
        deadline_text="The claim window closes on 2026-12-01.",
        tasks=["Follow the official account", "Join the discord"],
        eligibility=["Must hold 100 tokens"],
        reward_status="stated",
        reward_text="4.4 billion $META",
        reward_note="Jumlah hadiah disebut eksplisit oleh sumber.",
        zero_capital_ok=True,
        zero_capital_reason="Hanya aksi off-chain: follow, join",
        risk_score=0,
        risk_band="low",
        risk_explanation=["Tidak ada indikator risiko yang cocok di teks sumber."],
        cross_checked=True,
        cross_check_note="Disebut oleh 2 sumber berbeda: a.example, b.example",
    )


def _sample_health():
    from discovery import SourceHealth

    return SourceHealth(
        url="https://airdropalert.com/feed/rssfeed",
        reachable=True,
        entries_analyzed=15,
        campaigns_found=2,
        source_id="airdropalert-rss",
        label="AirdropAlert campaign feed",
        category="airdrop_listing",
        official=False,
        http_status=200,
        content_type="application/rss+xml",
    )


def test_report_carries_v13_structure():
    report = build_report(
        scan_time="2026-10-28T00:00:00+00:00",
        campaigns=[_sample_campaign()],
        source_health=[_sample_health()],
        wallet={"sol_balance": 1.5, "spl_token_count": 1, "spl_status": "ok"},
        errors=[],
        duplicates_skipped=3,
        total_found=9,
        state_source="run:123",
    )
    assert report["version"] == "1.3"
    assert report["state_source"] == "run:123"
    assert report["campaign_count"] == 1
    assert report["total_found"] == 9
    assert report["duplicates_skipped"] == 3
    assert report["source_summary"] == {
        "total": 1,
        "reachable": 1,
        "official_sources": 0,
    }
    assert set(report["opportunity_summary"]) == {
        "CONFIRMED_AIRDROP",
        "POINTS_PROGRAM",
        "TESTNET_POTENTIAL",
        "FREE_QUEST",
        "UNVERIFIED",
    }
    assert report["opportunity_summary"]["CONFIRMED_AIRDROP"] == 1
    assert report["zero_capital_summary"] == {"passes": 1, "blocked": 0, "unknown": 0}
    assert report["disclaimer"].endswith("Selalu verifikasi manual.")


def test_campaign_entry_separates_evidence_from_heuristics():
    report = build_report(
        scan_time="now",
        campaigns=[_sample_campaign()],
        source_health=[],
        wallet={},
        errors=[],
    )
    entry = report["campaigns"][0]

    # Quoted from the source.
    for key in (
        "title",
        "project",
        "url",
        "official_url",
        "announcement_url",
        "published",
        "tasks",
        "eligibility",
        "reward_status",
        "reward_text",
        "deadline",
        "deadline_text",
        "requirements",
    ):
        assert key in entry
    assert entry["deadline"] == "2026-12-01"
    assert entry["tasks"] == ["Follow the official account", "Join the discord"]
    assert entry["reward_status"] == "stated"

    # Labelled heuristics.
    for key in (
        "campaign_id",
        "opportunity_type",
        "opportunity_reason",
        "zero_capital_ok",
        "zero_capital_reason",
        "risk_score",
        "risk_band",
        "risk_explanation",
        "classification",
        "cross_checked",
        "cross_check_note",
    ):
        assert key in entry
    assert entry["campaign_id"]
    assert entry["risk_score"] == 0
    assert entry["zero_capital_ok"] is True


def test_missing_data_is_null_not_zero(tmp_path):
    from discovery import Campaign

    campaign = Campaign(title="Bare", project="", url="", source="https://f/rss")
    report = build_report(
        scan_time="now",
        campaigns=[campaign],
        source_health=[],
        wallet={"sol_balance": None, "spl_token_count": None, "spl_status": "unavailable"},
        errors=[],
    )
    path = write_json_report(report, path=tmp_path / "r.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["campaigns"][0]
    assert entry["deadline"] is None
    assert entry["official_url"] == ""
    assert entry["tasks"] == []
    assert data["wallet"]["sol_balance"] is None
    assert data["wallet"]["spl_token_count"] is None


def test_markdown_renders_the_v13_sections(tmp_path):
    report = build_report(
        scan_time="2026-10-28T00:00:00+00:00",
        campaigns=[_sample_campaign()],
        source_health=[_sample_health()],
        wallet={"sol_balance": 1.5, "spl_token_count": 1, "spl_status": "ok"},
        errors=["Source error (x): HTTP 500"],
        state_source="run:123",
    )
    path = write_markdown_report(report, path=tmp_path / "r.md")
    text = path.read_text(encoding="utf-8")

    assert "# LUMAVUN Crypto Agent V1.3" in text
    assert "`run:123`" in text
    assert "## Ringkasan tipe peluang" in text
    assert "**CONFIRMED_AIRDROP:** 1" in text
    assert "## Filter zero-capital" in text
    assert "**Lolos:** 1" in text
    assert "Tipe peluang:** CONFIRMED_AIRDROP" in text
    assert "Deadline:** 2026-12-01" in text
    assert "Status hadiah:** stated" in text
    assert "Aksi yang diminta:" in text
    assert "Verifikasi silang:** ya" in text
    assert "Risiko:** 0/100" in text
    assert "Source error (x): HTTP 500" in text
    assert "baca-only" in text


def test_markdown_reports_empty_scan_honestly(tmp_path):
    report = build_report(
        scan_time="now",
        campaigns=[],
        source_health=[],
        wallet={"sol_balance": None, "spl_token_count": None, "spl_status": "unavailable"},
        errors=[],
    )
    path = write_markdown_report(report, path=tmp_path / "empty.md")
    text = path.read_text(encoding="utf-8")
    assert "Tidak ada kampanye baru" in text
    assert "tidak tersedia" in text
    # Counters are shown, not omitted, when a scan finds nothing.
    assert "- **Duplikat dilewati:** 0" in text
    assert "**CONFIRMED_AIRDROP:** 0" in text
    assert "**Lolos:** 0" in text
