"""Tests for scripts/check_address.py."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_address.py"

spec = importlib.util.spec_from_file_location("check_address", SCRIPT)
check_address = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_address)

VALID = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
# Real repository value at the time of writing: contains 'I' and 'l', so it is
# not Base58 and cannot be a Solana public key.
NON_BASE58 = "4GEkhEsX2ut3ymVm2lIXVhv2ZhwMlP9kpCeZqBtVUTBA"
# Base58 alphabet, correct length, but decodes to 31 bytes.
TOO_FEW_BYTES = "2aNsVvhbn7baZeiMTcAhgvVFzicwhyDwcSxNxMM8rf2"


class TestExplain:
    def test_valid_address(self):
        assert check_address.explain(VALID) == "ok"

    def test_non_base58_characters_are_named(self):
        reason = check_address.explain(NON_BASE58)
        assert "outside the Base58 alphabet" in reason
        assert "'I'" in reason or "'l'" in reason

    def test_wrong_decoded_length_is_reported(self):
        reason = check_address.explain(TOO_FEW_BYTES)
        assert "31 bytes" in reason

    def test_empty_value(self):
        assert check_address.explain("") == "empty value"


class TestMain:
    def test_valid_address_returns_zero(self, capsys):
        assert check_address.main([VALID]) == 0
        out = capsys.readouterr().out
        assert "VALID" in out

    def test_invalid_address_returns_nonzero(self, capsys):
        assert check_address.main([NON_BASE58]) == 1
        out = capsys.readouterr().out
        assert "INVALID" in out
        assert "reason:" in out

    def test_mixed_input_fails(self):
        assert check_address.main([VALID, NON_BASE58]) == 1

    def test_no_arguments_prints_usage(self, capsys):
        assert check_address.main([]) == 2
        assert "Usage" in capsys.readouterr().out

    def test_env_mode_reads_variable(self, monkeypatch, capsys):
        monkeypatch.setenv("PHANTOM_PUBLIC_ADDRESS", VALID)
        assert check_address.main(["--env"]) == 0
        assert "VALID" in capsys.readouterr().out

    def test_env_mode_reports_missing_variable(self, monkeypatch):
        monkeypatch.delenv("PHANTOM_PUBLIC_ADDRESS", raising=False)
        assert check_address.main(["--env"]) == 2
