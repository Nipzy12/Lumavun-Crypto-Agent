"""
Phantom wallet monitor (V1.3).

Reads the native SOL balance and read-only SPL token balances through
Solana JSON-RPC. It validates addresses with Base58 decoding and a 32-byte
length check, handles errors and rate limits, supports configurable
fallback endpoints, and never requests or stores secrets. It never signs
anything and never asks for a private key.

Three outcomes are kept distinct on purpose:

* **empty**       - the RPC answered and the balance is 0. Reported as 0 SOL.
* **unavailable** - the RPC request itself failed. Reported as unavailable.
* **invalid**     - the configured address is not a valid public key.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from config_loader import load_config

DEFAULT_RPC_TIMEOUT = 20
ADDRESS_REGEX = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss6"


def validate_solana_address(address: str) -> bool:
    """Validate a public Solana address.

    Checks the Base58 alphabet, then decodes and verifies the decoded
    payload is exactly 32 bytes (an Ed25519 pubkey). This accepts real
    Phantom public addresses and rejects typos, wrong-length strings, and
    non-Base58 text.
    """
    if not address:
        return False
    raw = address.strip()
    if not ADDRESS_REGEX.match(raw):
        return False
    try:
        from base58 import b58decode

        decoded = b58decode(raw)
    except Exception:
        return False
    if len(decoded) != 32:
        return False
    return True


def _rpc_post(
    url: str,
    payload: Dict[str, Any],
    timeout: int = DEFAULT_RPC_TIMEOUT,
) -> Dict[str, Any]:
    """POST a JSON-RPC request and return the parsed body.

    Raises RuntimeError for HTTP errors and for explicit RPC errors.
    """
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"RPC request failed: {type(exc).__name__}") from exc

    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError("RPC response was not valid JSON") from exc

    if not isinstance(body, dict):
        raise RuntimeError("RPC response was not a JSON object")

    if "error" in body:
        error = body["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(f"RPC error: {message}")

    return body


def check_rpc_health(url: str, timeout: int = DEFAULT_RPC_TIMEOUT) -> Tuple[bool, str]:
    """Probe an RPC endpoint with getHealth."""
    try:
        body = _rpc_post(
            url,
            {"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            timeout=timeout,
        )
        status = body.get("result", {})
        if isinstance(status, dict):
            epoch = str(status.get("epoch", ""))
            return True, f"ok (epoch {epoch})"
        return True, "ok"
    except RuntimeError as exc:
        return False, str(exc)[:160]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def get_sol_balance(
    address: str,
    rpc_url: str,
    timeout: int = DEFAULT_RPC_TIMEOUT,
) -> Optional[float]:
    """Read native SOL balance for a public address.

    Returns the balance as a float (which may be 0.0 when the RPC succeeds
    and the account has no lamports), or None only when the request fails.
    """
    if not validate_solana_address(address):
        return None
    try:
        body = _rpc_post(
            rpc_url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [address.strip()],
            },
            timeout=timeout,
        )
        result = body.get("result")
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        if value is None:
            return None
        return int(value) / 1_000_000_000
    except RuntimeError:
        return None
    except Exception:
        return None


def _parse_token_amount(account: Any) -> Optional[Dict[str, Any]]:
    """Extract mint/amount from one token account entry.

    Handles the ``jsonParsed`` shapes used by public RPC endpoints, where
    the details live at ``account.data.parsed.info`` (and, on some older
    proxies, at ``account.data.token_info``).
    """
    if not isinstance(account, dict):
        return None
    inner = account.get("account")
    if not isinstance(inner, dict):
        return None
    data = inner.get("data")

    info: Dict[str, Any] = {}
    if isinstance(data, dict):
        parsed = data.get("parsed")
        if isinstance(parsed, dict) and isinstance(parsed.get("info"), dict):
            info = parsed["info"]
        elif isinstance(data.get("token_info"), dict):
            info = data["token_info"]

    mint = str(info.get("mint", "") or "")
    if not mint:
        return None

    amount_block = info.get("tokenAmount")
    raw_amount = None
    decimals = None
    ui_amount = None
    if isinstance(amount_block, dict):
        raw_amount = amount_block.get("amount")
        decimals = amount_block.get("decimals")
        ui_amount = amount_block.get("uiAmount")
    else:
        raw_amount = info.get("raw_balance")
        decimals = info.get("decimals")

    if ui_amount is None and raw_amount is not None and decimals is not None:
        try:
            ui_amount = int(raw_amount) / (10 ** int(decimals))
        except (ValueError, TypeError):
            ui_amount = None

    return {
        "mint": mint,
        "raw_balance": raw_amount,
        "decimals": decimals,
        "ui_amount": ui_amount,
    }


def query_token_accounts(
    address: str,
    rpc_url: str,
    mint_filter: Optional[List[str]] = None,
    timeout: int = DEFAULT_RPC_TIMEOUT,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Read SPL token accounts, returning ``(tokens, error)``.

    ``([], None)`` means the RPC answered and the wallet holds no token
    accounts. ``(None, reason)`` means the query failed, which must not be
    reported as an empty wallet.
    """
    if not validate_solana_address(address):
        return None, "Invalid Solana address format"
    try:
        body = _rpc_post(
            rpc_url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    address.strip(),
                    {"programId": TOKEN_PROGRAM_ID},
                    {"encoding": "jsonParsed"},
                ],
            },
            timeout=timeout,
        )
    except RuntimeError as exc:
        return None, str(exc)[:160]
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"

    result = body.get("result")
    if not isinstance(result, dict):
        return None, "RPC result was not an object"
    value = result.get("value")
    if not isinstance(value, list):
        return None, "RPC result had no token account list"

    filter_set = {m.strip().lower() for m in (mint_filter or []) if m.strip()}
    tokens: List[Dict[str, Any]] = []
    for account in value:
        parsed = _parse_token_amount(account)
        if parsed is None:
            continue
        if filter_set and parsed["mint"].lower() not in filter_set:
            continue
        tokens.append(parsed)
    return tokens, None


def get_token_accounts(
    address: str,
    rpc_url: str,
    mint_filter: Optional[List[str]] = None,
    timeout: int = DEFAULT_RPC_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Read read-only SPL token balances for an address.

    Kept for backward compatibility; use :func:`query_token_accounts` when
    the difference between "empty" and "failed" matters.
    """
    tokens, _ = query_token_accounts(address, rpc_url, mint_filter, timeout)
    return tokens or []


def read_wallet(
    address: str,
    primary_url: str,
    fallback_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Read SOL and SPL balances with fallback endpoints and diagnostics.

    A successful RPC call that returns a zero balance is reported as 0.0
    SOL, and an empty token list is reported as zero tokens. "Unavailable"
    is used only when the RPC request itself fails.
    """
    if not address:
        return {
            "address_valid": False,
            "sol_balance": None,
            "spl_token_count": None,
            "spl_tokens": [],
            "spl_status": "unavailable",
            "spl_query_ok": False,
            "diagnostics": "No address configured",
        }

    if not validate_solana_address(address):
        return {
            "address_valid": False,
            "sol_balance": None,
            "spl_token_count": None,
            "spl_tokens": [],
            "spl_status": "unavailable",
            "spl_query_ok": False,
            "diagnostics": "Invalid Solana address format",
        }

    endpoints = _build_endpoint_chain(primary_url, fallback_urls)
    used_endpoint: Optional[str] = None
    diagnostics_parts: List[str] = []

    for url in endpoints:
        healthy, health_msg = check_rpc_health(url)
        diagnostics_parts.append(f"{url[:70]}: {health_msg}")
        if healthy:
            used_endpoint = url
            break

    if not used_endpoint:
        return {
            "address_valid": True,
            "sol_balance": None,
            "spl_token_count": None,
            "spl_tokens": [],
            "spl_status": "unavailable",
            "spl_query_ok": False,
            "diagnostics": "All RPC endpoints unhealthy: " + "; ".join(diagnostics_parts),
        }

    sol_balance = get_sol_balance(address, used_endpoint)
    if sol_balance is None:
        diagnostics_parts.append(f"SOL read failed via {used_endpoint[:70]}")

    tokens, token_error = query_token_accounts(address, used_endpoint)
    if token_error:
        diagnostics_parts.append(f"SPL read failed: {token_error}")

    if tokens is None:
        spl_status = "unavailable"
        spl_count: Optional[int] = None
        spl_note = token_error or "SPL query failed"
        spl_tokens: List[Dict[str, Any]] = []
        non_zero = None
    else:
        spl_status = "ok"
        spl_count = len(tokens)
        non_zero = sum(
            1
            for token in tokens
            if isinstance(token.get("ui_amount"), (int, float)) and token["ui_amount"] > 0
        )
        spl_note = (
            f"{spl_count} akun token, {non_zero} dengan saldo > 0"
            if spl_count
            else "Tidak ada akun SPL token (saldo kosong, RPC berhasil)"
        )
        spl_tokens = sorted(
            tokens,
            key=lambda t: t.get("ui_amount") or 0,
            reverse=True,
        )[:50]

    return {
        "address_valid": True,
        "sol_balance": sol_balance,
        "spl_token_count": spl_count,
        "spl_nonzero_count": non_zero,
        "spl_tokens": spl_tokens,
        "spl_status": spl_status,
        "spl_query_ok": tokens is not None,
        "spl_note": spl_note,
        "used_rpc": used_endpoint,
        "diagnostics": "RPC checks: " + "; ".join(diagnostics_parts),
    }


def _build_endpoint_chain(
    primary: str,
    fallbacks: Optional[List[str]],
) -> List[str]:
    chain: List[str] = []
    if primary and isinstance(primary, str):
        primary = primary.strip()
        if primary:
            chain.append(primary)
    if fallbacks:
        for url in fallbacks:
            url = (url or "").strip()
            if url and url not in chain:
                chain.append(url)
    if not chain:
        config = load_config()
        default_url = config.get("rpc", {}).get("default_url", "")
        if default_url:
            chain.append(default_url)
    return chain
