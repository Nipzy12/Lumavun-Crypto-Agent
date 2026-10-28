"""
Phantom wallet monitor (V1.2).

Reads native SOL balance and, where supported, read-only SPL token
balances through Solana JSON-RPC. It validates addresses with Base58
decoding and a 32-byte length check, handles errors and rate limits,
supports configurable fallback endpoints, and never requests or stores
secrets.

A successful RPC response with a zero balance is reported as 0 SOL, not
as unavailable. "Unavailable" is reserved for failed requests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from config_loader import load_config

DEFAULT_RPC_TIMEOUT = 20
ADDRESS_REGEX = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def validate_solana_address(address: str) -> bool:
    """Validate a public Solana address.

    Checks Base58 alphabet, then decodes and verifies the decoded payload
    is exactly 32 bytes (Ed25519 pubkey). This accepts real Phantom public
    addresses and rejects typos, wrong-length strings, and non-Base58 text.
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
    # Solana Ed25519 pubkeys are 32 bytes.
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


def get_token_accounts(
    address: str,
    rpc_url: str,
    mint_filter: Optional[List[str]] = None,
    timeout: int = DEFAULT_RPC_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Read read-only SPL token balances for an address.

    Results are returned as a list of dicts with mint, raw balance, and
    decimals where recoverable. This does not sign anything and does not
    infer eligibility.
    """
    if not validate_solana_address(address):
        return []
    try:
        accounts = _get_token_accounts_for_owner(rpc_url, address.strip(), timeout)
    except RuntimeError:
        return []
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    filter_set = {m.strip().lower() for m in (mint_filter or []) if m.strip()}
    for account in accounts:
        info = account.get("account", {})
        token_info = info.get("token_info", {}) if isinstance(info, dict) else {}
        mint = (
            str(token_info.get("mint", ""))
            if isinstance(token_info, dict)
            else ""
        )
        if filter_set and mint.lower() not in filter_set:
            continue
        out.append({
            "mint": mint,
            "raw_balance": info.get("raw_balance") if isinstance(info, dict) else None,
            "decimals": info.get("decimals") if isinstance(info, dict) else None,
        })
    return out


def _get_token_accounts_for_owner(
    rpc_url: str,
    address: str,
    timeout: int,
) -> List[Dict[str, Any]]:
    """Fetch token accounts for owner using getTokenAccountsByOwner."""
    body = _rpc_post(
        rpc_url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss6"},
                {"encoding": "jsonParsed"},
            ],
        },
        timeout=timeout,
    )
    result = body.get("result")
    if not isinstance(result, dict):
        return []
    return result.get("value", [])


def read_wallet(
    address: str,
    primary_url: str,
    fallback_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Read SOL balance with fallback endpoints and diagnostics.

    Returns a dict with balance information plus a health/diagnostic
    summary. A successful RPC call that returns a zero balance is reported
    as 0.0 SOL. "Unavailable" is used only when the RPC request itself
    fails.
    """
    if not address:
        return {
            "address_valid": False,
            "sol_balance": None,
            "spl_token_count": 0,
            "spl_tokens": [],
            "diagnostics": "No address configured",
        }

    address_valid = validate_solana_address(address)
    if not address_valid:
        return {
            "address_valid": False,
            "sol_balance": None,
            "spl_token_count": 0,
            "spl_tokens": [],
            "diagnostics": "Invalid Solana address format",
        }

    endpoints = _build_endpoint_chain(primary_url, fallback_urls)
    sol_balance: Optional[float] = None
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
            "spl_token_count": 0,
            "spl_tokens": [],
            "diagnostics": "All RPC endpoints unhealthy: " + "; ".join(
                diagnostics_parts
            ),
        }

    sol_balance = get_sol_balance(address, used_endpoint)
    if sol_balance is None:
        diagnostics_parts.append(f"SOL read failed via {used_endpoint[:70]}")

    # Read-only SPL token balances are best-effort and may be unavailable
    # on public endpoints.
    try:
        tokens = get_token_accounts(address, used_endpoint)
    except Exception:
        tokens = []

    return {
        "address_valid": True,
        "sol_balance": sol_balance,
        "spl_token_count": len(tokens),
        "spl_tokens": tokens[:50],
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
