"""Official API research gate: read-only, evidence-driven and default-deny."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from claim_api_audit import load_research, audit_report, approved_claim_adapters
from claim_engine import adapters

NOW = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)


def test_qualified_campaign_count_is_zero():
    research = load_research(now=NOW)
    report = audit_report(now=NOW)
    assert report["reviewed_sources"] == 7
    assert report["excluded"] == 7
    assert report["approved_claim_apis"] == 0
    assert report["payouts_confirmed_from_this_audit"] == 0
    assert approved_claim_adapters(now=NOW) == {}
    assert adapters(now=NOW) == {}
    assert "CLAIM_FEES_AND_NO_PARTICIPANT_AUTOCLAIM_API" in report["rejection_reasons"]
    assert any(n["source_id"] == "galxe-quest" and n["claim_deadline"] == "2026-12-15"
               for n in report["notices"])


def save(tmp_path, value):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_false_positive_approval_needs_mainnet_and_permission(tmp_path):
    r = deepcopy(load_research(now=NOW))
    target = r["candidates"][0]
    target["status"] = "APPROVED"
    target["api_role"] = "PARTICIPANT_CLAIM"
    target["mainnet_income"] = True
    target["official_claim_contract"] = True
    target["gasless_for_participant"] = True
    target["automation_allowed"] = True
    # Still blocked: Galxe's mixed network is not a verified Solana claim.
    with pytest.raises(ValueError, match="Approved candidate"):
        load_research(save(tmp_path, r), now=NOW)


def test_unvetted_claim_adapter_rejected_even_with_optimistic_flags(tmp_path):
    adapter = {
        "id": "example_official_quest", "campaign_id": "a"*24,
        "official_url": "https://example.org/campaign",
        "api_origin": "https://api.example.org",
        "eligibility_path": "/v1/entitlement",
        "claim_path": "/v1/claim",
        "docs_url": "https://api.example.org/docs",
        "terms_url": "https://example.org/terms",
        "checked_at": NOW.isoformat(),
        "automation_allowed": True, "gasless_verified": True,
        "no_mandatory_fees_verified": True, "network": "solana-mainnet",
        "requires_token": False,
    }
    config = tmp_path / "adapters.json"
    config.write_text(json.dumps({"schema_version": 1, "adapters": [adapter]}), encoding="utf-8")
    with pytest.raises(ValueError, match="no matching independent approved"):
        adapters(config, now=NOW)


def test_duplicate_candidates_do_not_pass(tmp_path):
    r = deepcopy(load_research(now=NOW))
    r["candidates"].append(deepcopy(r["candidates"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        load_research(save(tmp_path, r), now=NOW)


def test_bad_documentation_url_is_rejected(tmp_path):
    r = deepcopy(load_research(now=NOW))
    r["candidates"][0]["document_url"] = "http://127.0.0.1/claim"
    with pytest.raises(ValueError, match="documentation"):
        load_research(save(tmp_path, r), now=NOW)


def test_future_review_date_cannot_be_represented_as_verified(tmp_path):
    r = deepcopy(load_research(now=NOW))
    r["observed_at"] = "2026-11-17"
    with pytest.raises(ValueError, match="audit date"):
        load_research(save(tmp_path, r), now=NOW)


def test_positive_approval_cannot_appear_without_source_binding(tmp_path):
    r = deepcopy(load_research(now=NOW))
    r["approved_adapters"].append({
        "id": "fabricated", "campaign_id": "a"*24,
        "official_url": "https://example.org/campaign",
        "api_origin": "https://api.example.org",
        "docs_url": "https://api.example.org/docs",
        "automation_permission_evidence": "https://example.org/allowed",
        "gas_and_fee_evidence": "https://example.org/cost",
        "participant_eligibility_evidence": "https://example.org/eligibility",
        "checked_at": NOW.isoformat(),
    })
    with pytest.raises(ValueError, match="positive candidate"):
        load_research(save(tmp_path, r), now=NOW)


def test_staleness_is_reported_not_hidden():
    older = datetime(2026, 11, 27, tzinfo=timezone.utc)
    report = audit_report(now=older)
    assert report["stale_review"] is True
    assert report["approved_claim_apis"] == 0
