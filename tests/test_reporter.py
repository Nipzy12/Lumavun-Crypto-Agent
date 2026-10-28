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
