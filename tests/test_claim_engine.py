"""Regression tests for the zero-capital claim engine. No live claim occurs."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import json
import pytest

from claim_engine import (
    adapters, validate_adapter, gate, authorize, revoke, current_approval,
    eligible_to_dispatch, verify_entitlement, submit_claim, claim_report, utcnow,
)
from triage import revision

ID = "a"*24
WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"


@pytest.fixture
def adapter():
    return {
        "id": "example_official_quest", "campaign_id": ID,
        "official_url": "https://example.org/campaign",
        "api_origin": "https://api.example.org",
        "eligibility_path": "/v1/entitlement",
        "claim_path": "/v1/claim",
        "docs_url": "https://api.example.org/docs",
        "terms_url": "https://example.org/terms",
        "checked_at": (utcnow()-timedelta(minutes=2)).isoformat(),
        "automation_allowed": True, "gasless_verified": True,
        "no_mandatory_fees_verified": True, "requires_token": True,
    }


@pytest.fixture
def record():
    return {
        "id": ID, "official_url": "https://example.org/campaign",
        "operations": {"state": "AWAITING_RESULT"},
        "tasks": {"task1": {"completion": {"kind": "human_confirmation", "at": "2026-11-15",
                                           "evidence": "Human inspected proof"},
                             "approval_reasons": [], "approval": None}},
        "operator_history": [], "terms_review_required": False,
        "evidence_verified": True,
    }


@pytest.fixture
def qualified(monkeypatch):
    fields = {k: {"status": "PASS"} for k in
              ("mandatory_fees", "gas_costs", "geographic_eligibility",
               "account_eligibility", "official_url")}
    q = {"status": "ACTIONABLE", "reward_class": "CONDITIONAL_TOKEN_REWARD", "fields": fields}
    monkeypatch.setattr("claim_engine.qualify", lambda record, now=None: deepcopy(q))
    monkeypatch.setattr("claim_engine.classify",
                        lambda record, qualification: {"participation_cost": "FREE_VERIFIED",
                                                        "participation_model": "individual quest"})
    return q


def test_default_registry_is_empty():
    assert adapters() == {}


def test_adapter_rejects_unreviewed_permissions(adapter):
    for key in ("automation_allowed", "gasless_verified", "no_mandatory_fees_verified"):
        changed = dict(adapter, **{key: False})
        with pytest.raises(ValueError):
            validate_adapter(changed)
    with pytest.raises(ValueError):
        validate_adapter(dict(adapter, api_origin="http://127.0.0.1"))


def test_adapter_rejects_unknown_fields_and_stale_review(adapter):
    with pytest.raises(ValueError):
        validate_adapter(dict(adapter, injected="override"))
    with pytest.raises(ValueError):
        validate_adapter(dict(adapter, checked_at="2025-01-01T00:00:00+00:00"))
    with pytest.raises(ValueError):
        validate_adapter(dict(adapter, claim_path="//evil.example/p"))


def test_default_never_authorizes_claim(record, adapter, qualified):
    assert "NO_CURRENT_CAMPAIGN_APPROVAL" in eligible_to_dispatch(record, adapter, WALLET)
    assert claim_report({"quest_queue": {ID: record}}, {}, wallet=WALLET)["ready"] == 0


def test_gate_blocks_missing_wallet_and_unfinished_human_work(record, adapter, qualified):
    assert "PUBLIC_WALLET_REQUIRED" in gate(record, adapter, wallet="")
    record["tasks"]["task1"]["completion"] = None
    assert "TASK_PROOFS_INCOMPLETE" in gate(record, adapter, wallet=WALLET)


def test_gate_blocks_education_grants_and_unknown_cost(record, adapter, qualified, monkeypatch):
    monkeypatch.setattr("claim_engine.classify",
                        lambda rec, qual: {"participation_cost": "COST_UNKNOWN",
                                           "participation_model": "developer grant"})
    assert "NOT_ZERO_CAPITAL_INDIVIDUAL_QUEST" in gate(record, adapter, wallet=WALLET)


def test_authorization_needs_correct_revision_and_is_one_time(record, adapter, qualified):
    with pytest.raises(ValueError):
        authorize(record, adapter, wallet=WALLET, actor="operator", expected_revision="old", request_id="claim-auth1")
    rev = revision(record)
    approve = authorize(record, adapter, wallet=WALLET, actor="operator", expected_revision=rev,
                        request_id="claim-auth1")
    assert approve["kind"] == "CLAIM_AUTHORIZED"
    assert current_approval(record, adapter, wallet=WALLET)
    assert not current_approval(record, adapter, wallet="9"*40)
    with pytest.raises(ValueError):
        authorize(record, adapter, wallet=WALLET, actor="operator", expected_revision=rev,
                  request_id="claim-auth1")
    assert eligible_to_dispatch(record, adapter, WALLET) == []


def test_revoke_and_change_terms_invalidate_approval(record, adapter, qualified):
    authorize(record, adapter, wallet=WALLET, actor="operator",
              expected_revision=revision(record), request_id="claim-auth2")
    record["terms_review_required"] = True
    assert not current_approval(record, adapter, wallet=WALLET)
    record["terms_review_required"] = False
    revoke(record, actor="operator", request_id="claim-revoke2")
    assert not current_approval(record, adapter, wallet=WALLET)


def test_once_dispatched_cannot_retry_even_if_api_result_missing(record, adapter, qualified):
    authorize(record, adapter, wallet=WALLET, actor="operator",
              expected_revision=revision(record), request_id="claim-auth3")
    record["operator_history"].append({"id": "claim-attempt3", "kind": "CLAIM_DISPATCHED", "at": utcnow().isoformat()})
    assert "ALREADY_DISPATCHED_REVIEW_REQUIRED" in eligible_to_dispatch(record, adapter, WALLET)


class FakeResponse:
    def __init__(self, obj, status=200):
        self.body = json.dumps(obj).encode()
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size=8192):
        yield self.body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.replies.pop(0)


def test_live_entitlement_then_actual_http_claim(record, adapter, qualified, monkeypatch):
    monkeypatch.setattr("source_security.check_source_destination", lambda url: True)
    s = FakeSession([
        FakeResponse({"campaign_id": ID, "wallet": WALLET, "eligible": True,
                      "claimable": True, "active": True,
                      "fee_required": False, "gas_required": False}),
        FakeResponse({"campaign_id": ID, "wallet": WALLET,
                      "claim_id": "official-claim-123", "status": "accepted"}),
    ])
    assert verify_entitlement(adapter, record, wallet=WALLET, session=s, token="provider-token")
    result = submit_claim(adapter, record, wallet=WALLET, request_id="claim-attempt5",
                          session=s, token="provider-token")
    assert result["status"] == "CLAIM_SUBMITTED_UNVERIFIED"
    assert "not a received wallet reward" in result["note"]
    assert [x[0] for x in s.calls] == ["GET", "POST"]
    assert s.calls[1][2]["headers"]["Idempotency-Key"] == "claim-attempt5"
    assert all(x[2]["allow_redirects"] is False for x in s.calls)


@pytest.mark.parametrize("mutate", [
    {"eligible": False}, {"claimable": False}, {"active": False},
    {"gas_required": True}, {"fee_required": True}, {"wallet": "mismatch"},
    {"campaign_id": "b"*24},
])
def test_live_claim_preflight_fails_closed(record, adapter, monkeypatch, mutate):
    monkeypatch.setattr("source_security.check_source_destination", lambda url: True)
    obj = {"campaign_id": ID, "wallet": WALLET, "eligible": True,
           "claimable": True, "active": True, "fee_required": False, "gas_required": False}
    obj.update(mutate)
    session = FakeSession([FakeResponse(obj)])
    with pytest.raises(ValueError):
        verify_entitlement(adapter, record, wallet=WALLET, session=session, token="valid-token")


def test_live_claim_requires_api_key_when_configured(record, adapter, monkeypatch):
    monkeypatch.setattr("source_security.check_source_destination", lambda url: True)
    session = FakeSession([])
    with pytest.raises(ValueError):
        verify_entitlement(adapter, record, wallet=WALLET, session=session)
    assert session.calls == []


def test_bad_provider_response_is_never_paid_or_confirmed(record, adapter, monkeypatch):
    monkeypatch.setattr("source_security.check_source_destination", lambda url: True)
    bad = FakeSession([FakeResponse({"campaign_id": ID, "wallet": WALLET,
                                     "claim_id": "foo", "status": "paid"})])
    with pytest.raises(ValueError):
        submit_claim(adapter, record, wallet=WALLET,
                     request_id="claim-test", session=bad, token="provider-token")


def test_live_request_rejects_private_destination(record, adapter, monkeypatch):
    monkeypatch.setattr("source_security.check_source_destination", lambda url: False)
    s = FakeSession([])
    with pytest.raises(ValueError):
        verify_entitlement(adapter, record, wallet=WALLET, session=s, token="provider-token")
    assert s.calls == []
