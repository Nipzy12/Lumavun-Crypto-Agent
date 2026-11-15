#!/usr/bin/env python3
"""Export factual official claim-API research without calling a provider or claiming."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from claim_api_audit import audit_report
from claim_engine import adapters


def main():
    report = audit_report()
    real_adapters = adapters()
    if report["approved_claim_apis"] != len(real_adapters):
        raise ValueError("Audited approvals and active adapters mismatch; refusing unsafe release")
    report["active_claim_adapters"] = len(real_adapters)
    report["claim_execution_enabled_by_audit"] = False
    Path("claim_api_research_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "sources_reviewed": report["reviewed_sources"],
        "excluded": report["excluded"],
        "approved": report["approved_claim_apis"],
        "stale": report["stale_review"],
        "live_claims_sent": 0,
    }))


if __name__ == "__main__":
    main()
