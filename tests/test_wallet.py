import pytest
import requests
import requests_mock

from wallet import (
    validate_solana_address,
    read_wallet,
    get_sol_balance,
    get_token_accounts,
    check_rpc_health,
)

# A real, well-formed Solana Ed25519 public key (44 chars, 32 decoded bytes).
VALID_PHANTOM_ADDRESS = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"

# Base58 that is alphabet-valid and 43 characters long, but decodes to only
# 31 bytes. This is the case the 32-byte length check exists to catch.
BASE58_31_BYTES = "2aNsVvhbn7baZeiMTcAhgvVFzicwhyDwcSxNxMM8rf2"


class TestValidateAddress:
    def test_accepts_valid_base58_32_byte_address(self):
        assert validate_solana_address(VALID_PHANTOM_ADDRESS) is True

    def test_accepts_address_with_surrounding_whitespace(self):
        assert validate_solana_address(f"  {VALID_PHANTOM_ADDRESS}  ") is True

    def test_rejects_base58_that_decodes_to_wrong_byte_length(self):
        # Passes the character-class/length prefilter but is 31 bytes, so the
        # decode-based length check must reject it.
        import re

        assert re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", BASE58_31_BYTES)
        assert validate_solana_address(BASE58_31_BYTES) is False

    def test_rejects_short_string(self):
        assert validate_solana_address("Abc123") is False

    def test_rejects_non_base58_characters(self):
        # '0', 'O', 'I' and 'l' are not in the Base58 alphabet.
        assert validate_solana_address("0OIl" + VALID_PHANTOM_ADDRESS[4:]) is False

    def test_rejects_empty_and_none(self):
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
            healthy, _ = check_rpc_health("https://rpc.example.com")
        assert healthy is False

    def test_rpc_error_in_response(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "rate limited"},
                    "id": 1,
                },
                status_code=200,
            )
            healthy, msg = check_rpc_health("https://rpc.example.com")
        assert healthy is False
        assert "rate limited" in msg.lower()


class TestGetSolBalance:
    def test_returns_balance_on_success(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "result": {"value": 500000000}, "id": 1},
                status_code=200,
            )
            sol = get_sol_balance(VALID_PHANTOM_ADDRESS, "https://rpc.example.com")
        assert sol == pytest.approx(0.5)

    def test_zero_lamports_returns_zero_not_none(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "result": {"value": 0}, "id": 1},
                status_code=200,
            )
            sol = get_sol_balance(VALID_PHANTOM_ADDRESS, "https://rpc.example.com")
        assert sol is not None
        assert sol == pytest.approx(0.0)

    def test_returns_none_on_rpc_error(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "rate limited"},
                    "id": 1,
                },
                status_code=200,
            )
            sol = get_sol_balance(VALID_PHANTOM_ADDRESS, "https://rpc.example.com")
        assert sol is None

    def test_returns_none_for_invalid_address(self):
        assert get_sol_balance("not-an-address", "https://rpc.example.com") is None


class TestReadWallet:
    def test_all_endpoints_unhealthy(self):
        with requests_mock.Mocker() as m:
            m.post("https://primary.example", exc=requests.exceptions.ConnectionError)
            m.post("https://fallback.example", exc=requests.exceptions.HTTPError)
            wallet = read_wallet(
                VALID_PHANTOM_ADDRESS,
                "https://primary.example",
                ["https://fallback.example"],
            )
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

    def test_healthy_rpc_with_zero_balance_reports_zero_sol(self):
        """RPC success + zero balance must read as 0 SOL, not 'unavailable'.

        This is the core V1.2 wallet requirement: an empty wallet is a real
        result, not an RPC failure.
        """
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                [
                    # 1) getHealth
                    {"json": {"jsonrpc": "2.0", "result": {"epoch": "1"}, "id": 1}, "status_code": 200},
                    # 2) getBalance -> 0 lamports, a successful response
                    {"json": {"jsonrpc": "2.0", "result": {"value": 0}, "id": 1}, "status_code": 200},
                    # 3) getTokenAccountsByOwner -> no token accounts
                    {"json": {"jsonrpc": "2.0", "result": {"value": []}, "id": 1}, "status_code": 200},
                ],
            )
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])

        assert wallet["address_valid"] is True
        assert wallet["sol_balance"] is not None
        assert wallet["sol_balance"] == pytest.approx(0.0)
        assert wallet["spl_token_count"] == 0
        assert "failed" not in wallet["diagnostics"].lower()

    def test_healthy_rpc_with_balance_reports_value(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                [
                    {"json": {"jsonrpc": "2.0", "result": {"epoch": "1"}, "id": 1}, "status_code": 200},
                    {"json": {"jsonrpc": "2.0", "result": {"value": 1250000000}, "id": 1}, "status_code": 200},
                    {"json": {"jsonrpc": "2.0", "result": {"value": []}, "id": 1}, "status_code": 200},
                ],
            )
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])

        assert wallet["sol_balance"] == pytest.approx(1.25)

    def test_fallback_endpoint_used_when_primary_fails(self):
        with requests_mock.Mocker() as m:
            m.post("https://primary.example", exc=requests.exceptions.ConnectionError)
            m.post(
                "https://fallback.example",
                [
                    {"json": {"jsonrpc": "2.0", "result": {"epoch": "9"}, "id": 1}, "status_code": 200},
                    {"json": {"jsonrpc": "2.0", "result": {"value": 0}, "id": 1}, "status_code": 200},
                    {"json": {"jsonrpc": "2.0", "result": {"value": []}, "id": 1}, "status_code": 200},
                ],
            )
            wallet = read_wallet(
                VALID_PHANTOM_ADDRESS,
                "https://primary.example",
                ["https://fallback.example"],
            )

        assert wallet["used_rpc"] == "https://fallback.example"
        assert wallet["sol_balance"] == pytest.approx(0.0)


class TestSplTokenStatus:
    """V1.3: an empty token list and a failed token query are different.

    Reporting "0 tokens" when the RPC actually failed is the mistake this
    class exists to prevent.
    """

    @staticmethod
    def _sequence(m, url, balance, token_payload):
        m.post(
            url,
            [
                {"json": {"jsonrpc": "2.0", "result": {"epoch": "3"}, "id": 1}, "status_code": 200},
                {"json": {"jsonrpc": "2.0", "result": {"value": balance}, "id": 1}, "status_code": 200},
                {"json": token_payload, "status_code": 200},
            ],
        )

    @staticmethod
    def _token(mint, amount, decimals, ui_amount=None):
        info = {"mint": mint, "tokenAmount": {"amount": amount, "decimals": decimals}}
        if ui_amount is not None:
            info["tokenAmount"]["uiAmount"] = ui_amount
        return {
            "jsonrpc": "2.0",
            "result": {
                "value": [
                    {
                        "pubkey": "5abc",
                        "account": {
                            "data": {"parsed": {"info": info, "program": "spl-token"}, "space": 165},
                        },
                    }
                ]
            },
            "id": 1,
        }

    def test_empty_wallet_is_zero_not_unavailable(self):
        with requests_mock.Mocker() as m:
            self._sequence(
                m,
                "https://rpc.example.com",
                0,
                {"jsonrpc": "2.0", "result": {"value": []}, "id": 1},
            )
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])
        assert wallet["spl_query_ok"] is True
        assert wallet["spl_status"] == "ok"
        assert wallet["spl_token_count"] == 0
        assert wallet["sol_balance"] == pytest.approx(0.0)
        assert "kosong" in wallet["spl_note"]

    def test_failed_spl_query_is_not_reported_as_an_empty_wallet(self):
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                [
                    {"json": {"jsonrpc": "2.0", "result": {"epoch": "3"}, "id": 1}, "status_code": 200},
                    {"json": {"jsonrpc": "2.0", "result": {"value": 1000000000}, "id": 1}, "status_code": 200},
                    {
                        "json": {
                            "jsonrpc": "2.0",
                            "error": {"code": -32005, "message": "rate limited"},
                            "id": 1,
                        },
                        "status_code": 200,
                    },
                ],
            )
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])

        # SOL still read successfully ...
        assert wallet["sol_balance"] == pytest.approx(1.0)
        # ... but the token section must say "unavailable", not "0".
        assert wallet["spl_query_ok"] is False
        assert wallet["spl_status"] == "unavailable"
        assert wallet["spl_token_count"] is None
        assert "SPL read failed" in wallet["diagnostics"]

    def test_token_ui_amount_is_derived_when_missing(self):
        payload = self._token("So11111111111111111111111111111111111111112", "1500000000", 9)
        with requests_mock.Mocker() as m:
            self._sequence(m, "https://rpc.example.com", 1, payload)
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])

        assert wallet["spl_token_count"] == 1
        token = wallet["spl_tokens"][0]
        assert token["mint"] == "So11111111111111111111111111111111111111112"
        assert token["decimals"] == 9
        assert token["ui_amount"] == pytest.approx(1.5)
        assert wallet["spl_nonzero_count"] == 1

    def test_legacy_token_info_shape_is_understood(self):
        payload = {
            "jsonrpc": "2.0",
            "result": {
                "value": [
                    {
                        "account": {
                            "data": {
                                "token_info": {
                                    "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                                    "raw_balance": 2500000,
                                    "decimals": 6,
                                }
                            }
                        }
                    }
                ]
            },
            "id": 1,
        }
        with requests_mock.Mocker() as m:
            self._sequence(m, "https://rpc.example.com", 1, payload)
            wallet = read_wallet(VALID_PHANTOM_ADDRESS, "https://rpc.example.com", [])

        assert wallet["spl_token_count"] == 1
        assert wallet["spl_tokens"][0]["ui_amount"] == pytest.approx(2.5)

    def test_query_token_accounts_distinguishes_empty_from_failed(self):
        from wallet import query_token_accounts

        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={"jsonrpc": "2.0", "result": {"value": []}, "id": 1},
                status_code=200,
            )
            tokens, error = query_token_accounts(
                VALID_PHANTOM_ADDRESS, "https://rpc.example.com"
            )
        assert tokens == []
        assert error is None

        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "rate limited"},
                    "id": 1,
                },
                status_code=200,
            )
            tokens, error = query_token_accounts(
                VALID_PHANTOM_ADDRESS, "https://rpc.example.com"
            )
        assert tokens is None
        assert "rate limited" in error

    def test_get_token_accounts_still_returns_a_list(self):
        """Backward compatibility: the old helper never raises."""
        with requests_mock.Mocker() as m:
            m.post(
                "https://rpc.example.com",
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "rate limited"},
                    "id": 1,
                },
                status_code=200,
            )
            tokens = get_token_accounts(VALID_PHANTOM_ADDRESS, "https://rpc.example.com")
        assert tokens == []
