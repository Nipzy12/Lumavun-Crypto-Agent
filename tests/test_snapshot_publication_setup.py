"""Worker snapshot publishing must report configuration gaps without failing scans."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_operator_snapshot.py"


def load_script():
    spec = importlib.util.spec_from_file_location("operator_snapshot_publisher_fixture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("latest_report.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    module = load_script()
    monkeypatch.setattr(module, "export_snapshot", lambda _: {"schema_version": 1, "generated_at": "2026-11-15T00:00:00Z"})
    return module, output


def test_worker_url_without_secret_reports_missing_credential(tmp_path, monkeypatch, requests_mock):
    module, output = setup_report(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_WORKER_URL", "https://lumavun-operator.nexoraz.workers.dev")
    monkeypatch.delenv("OPERATOR_SNAPSHOT_WRITE_TOKEN", raising=False)
    module.main()
    assert Path("operator_snapshot.json").is_file()
    assert output.read_text().splitlines()[-1] == "status=CREDENTIAL_NOT_CONFIGURED"
    assert not requests_mock.called


def test_no_url_and_no_secret_reports_unconfigured(tmp_path, monkeypatch, requests_mock):
    module, output = setup_report(tmp_path, monkeypatch)
    monkeypatch.delenv("OPERATOR_WORKER_URL", raising=False)
    monkeypatch.delenv("OPERATOR_SNAPSHOT_WRITE_TOKEN", raising=False)
    module.main()
    assert output.read_text().splitlines()[-1] == "status=UNCONFIGURED"
    assert not requests_mock.called


def test_secret_without_worker_url_is_a_configuration_error(tmp_path, monkeypatch, requests_mock):
    module, output = setup_report(tmp_path, monkeypatch)
    monkeypatch.delenv("OPERATOR_WORKER_URL", raising=False)
    monkeypatch.setenv("OPERATOR_SNAPSHOT_WRITE_TOKEN", "a"*40)
    with pytest.raises(ValueError, match="configuration invalid"):
        module.main()
    assert "status=FAILED" in output.read_text()
    assert not requests_mock.called


def test_authenticated_snapshot_publish(tmp_path, monkeypatch, requests_mock):
    module, output = setup_report(tmp_path, monkeypatch)
    base = "https://lumavun-operator.nexoraz.workers.dev"
    monkeypatch.setenv("OPERATOR_WORKER_URL", base)
    monkeypatch.setenv("OPERATOR_SNAPSHOT_WRITE_TOKEN", "b"*40)
    monkeypatch.setattr(module, "check_source_destination", lambda _: True)
    request = requests_mock.put(base + "/snapshot", status_code=200)
    module.main()
    assert output.read_text().splitlines()[-1] == "status=PUBLISHED"
    assert requests_mock.call_count == 1
    assert requests_mock.last_request.headers["Authorization"] == "Bearer " + "b"*40
    assert requests_mock.last_request.json()["schema_version"] == 1
