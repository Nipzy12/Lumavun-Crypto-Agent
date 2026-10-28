import pytest
import requests
import requests_mock
from wallet import (
    validate_solana_address,
    read_wallet,
    get_sol_balance,
    check_rpc_health,
)


class TestValidateAddress:
    def test_valid_base58(self):
        assert validate_solana_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU") is True

    def test_invalid_when_short(self):
        assert validate_solana_address("Abc123") is False

    def test_rejects_empty(self):
        assert validate_solana_address("") is False
        assert validate_solana_address(None) is False


class TestRpcHealth:
    def test_healthy_endpoint(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "result": {"epoch": "1234"}, "id": 1},
                status_code=200,
            )
            healthy, msg = check_rpc_health("https://rpc.example.com")
        assert healthy is True
        assert "ok" in msg.lower()

    def test_unhealthy_endpoint_http_error(self):
        with requests_mock.Mocker() as m:
            m.post("https://rpc.example.com", exc=requests.exceptions.HTTPError)
            healthy, msg = check_rpc_health("https://rpc.example.com")
        assert healthy is False

    def test_rpc_error_in_response(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "error": {"code": -32005, "message": "rate limited"}, "id": 1},
                status_code=200,
            )
            healthy, msg = check_rpc_health("https://rpc.example.com")
        assert healthy is False
        assert "rate limited" in msg.lower()


class TestGetSolBalance:
    def test_returns_nan_solana_on_success(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "result": {"value": 500000000}, "id": 1},
                status_code=200,
            )
            sol = get_sol_balance("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "https://rpc.example.com")
        assert sol == pytest.approx(0.5)

    def test_returns_none_on_rpc_error(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "error": {"code": -32005, "message": "rate limited"}, "id": 1},
                status_code=200,
            )
            sol = get_sol_balance("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "https://rpc.example.com")
        assert sol is None

    def test_returns_none_for_invalid_address(self):
        sol = get_sol_balance("not-an-address", "https://rpc.example.com")
        assert sol is None


class TestReadWallet:
    def test_all_endpoints_unhealthy(self):
        with requests_mock.Mocker() as m:
            m.post("https://primary.example", exc=requests.exceptions.ConnectionError)
            m.post("https://fallback.example", exc=requests.exceptions.HTTPError)
            wallet = read_wallet("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "https://primary.example", ["https://fallback.example"])
        assert wallet["sol_balance"] is None
        assert wallet["address_valid"] is True
        assert "unhealthy" in wallet["diagnostics"].lower()

    def test_missing_address_reported_not_zero(self):
        wallet = read_wallet("", "https://rpc.example.com")
        assert wallet["sol_balance"] is None
        assert wallet["address_valid"] is False
        assert "No address configured" in wallet["diagnostics"]

    def test_invalid_address_format_reported_not_zero(self):
        wallet = read_wallet("bad", "https://rpc.example.com")
        assert wallet["sol_balance"] is None
        assert wallet["address_valid"] is False
        assert "Invalid" in wallet["diagnostics"]
