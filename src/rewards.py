"""Finalized, read-only wallet observations; incoming assets are not earnings."""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from wallet import _rpc_post, validate_solana_address


def _token_balances(rows: list, owner: str) -> dict:
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("owner") != owner:
            continue
        amount = row.get("uiTokenAmount")
        if not isinstance(amount, dict) or not isinstance(row.get("mint"), str):
            raise ValueError("Malformed owned token balance")
        decimals = amount.get("decimals")
        if type(decimals) is not int or not 0 <= decimals <= 255 or not str(amount.get("amount", "")).isdigit():
            raise ValueError("Malformed token quantity")
        value = Decimal(amount["amount"]) / Decimal(10) ** decimals
        mint = row["mint"]
        result[mint] = result.get(mint, Decimal(0)) + value
    return result


def transaction_changes(transaction: dict, owner: str, signature: str) -> list[dict]:
    """Parse finalized transaction balance evidence, not an airdrop label."""
    if not isinstance(transaction, dict) or not isinstance(transaction.get("meta"), dict):
        raise ValueError("Missing transaction metadata")
    meta = transaction["meta"]
    if "err" not in meta:
        raise ValueError("Missing transaction outcome")
    if meta["err"] is not None:
        return []
    tx = transaction.get("transaction", {})
    if not isinstance(tx, dict) or not isinstance(tx.get("signatures"), list) or not isinstance(tx.get("message"), dict):
        raise ValueError("Malformed transaction body")
    if signature not in tx.get("signatures", []):
        raise ValueError("Transaction signature mismatch")
    keys = tx.get("message", {}).get("accountKeys")
    if not isinstance(keys, list):
        raise ValueError("Missing account keys")
    addresses = [key.get("pubkey") if isinstance(key, dict) else key for key in keys]
    changes = []
    if owner in addresses:
        index = addresses.index(owner)
        before, after = meta.get("preBalances"), meta.get("postBalances")
        if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(keys) or len(after) != len(keys):
            raise ValueError("Invalid SOL balances")
        if type(before[index]) is not int or type(after[index]) is not int or min(before[index], after[index]) < 0:
            raise ValueError("Invalid lamports")
        delta = Decimal(after[index] - before[index]) / Decimal(1_000_000_000)
        if delta:
            changes.append({"asset": "SOL", "amount": str(delta)})
    pre, post = meta.get("preTokenBalances"), meta.get("postTokenBalances")
    if pre is not None and post is not None:
        if not isinstance(pre, list) or not isinstance(post, list):
            raise ValueError("Invalid SPL balance lists")
        before, after = _token_balances(pre, owner), _token_balances(post, owner)
        for mint in sorted(before.keys() | after.keys()):
            delta = after.get(mint, Decimal(0)) - before.get(mint, Decimal(0))
            if delta:
                changes.append({"asset": mint, "amount": str(delta)})
    for change in changes:
        change.update(signature=signature, slot=transaction.get("slot"), block_time=transaction.get("blockTime"),
                      evidence="finalized_transaction_balance_delta", attribution="UNVERIFIED_INCOMING" if Decimal(change["amount"]) > 0 else "OUTGOING")
    return changes


def recent_transfers(address: str, rpc_url: str, known: set[str], limit: int = 20) -> tuple[list[dict], list[str], list[str]]:
    if not validate_solana_address(address) or not rpc_url:
        return [], [], ["Wallet address or RPC unavailable"]
    records, completed, errors = [], [], []
    try:
        response = _rpc_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                      "params": [address, {"limit": limit, "commitment": "finalized"}]}, timeout=10)
        signatures = response.get("result")
        if not isinstance(signatures, list):
            raise ValueError("Invalid signature list")
        if len(signatures) >= limit:
            errors.append("Recent transfer window reached its limit; history may be incomplete")
        for entry in reversed(signatures[:limit]):
            if not isinstance(entry, dict) or not isinstance(entry.get("signature"), str) or "err" not in entry:
                raise ValueError("Invalid signature entry")
            signature = entry["signature"]
            if signature in known:
                continue
            if entry["err"] is not None:
                completed.append(signature)
                continue
            if entry.get("confirmationStatus") != "finalized":
                continue
            try:
                body = _rpc_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                         "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]}, timeout=10)
                records.extend(transaction_changes(body.get("result"), address, signature))
                completed.append(signature)
            except RuntimeError:
                errors.append("Transaction RPC failed; remaining requests deferred to the next scan")
                break
            except (ValueError, TypeError, KeyError, InvalidOperation):
                errors.append("Transaction unavailable or invalid; will retry")
    except (RuntimeError, ValueError, TypeError, KeyError, InvalidOperation):
        errors.append("Read-only transfer RPC unavailable or invalid")
    return records, completed, errors


def update_rewards(state: dict, wallet: dict, address: str, *, config: dict | None = None, fetch=recent_transfers) -> dict:
    config = config or {}
    if not validate_solana_address(address):
        return {"status": "UNAVAILABLE", "actual_rewards_received": [], "errors": ["No valid wallet configured"]}
    owner_key = hashlib.sha256(address.encode()).hexdigest()[:24]
    stores = state.setdefault("reward_wallets", {})
    tracker = stores.setdefault(owner_key, {"assets": {}, "events": {}, "signatures": {}, "token_baseline": False})
    now = datetime.now(timezone.utc).isoformat()
    assets = {}
    if wallet.get("sol_balance") is not None:
        assets["SOL"] = str(wallet["sol_balance"])
    if wallet.get("spl_status") == "ok":
        totals = {}
        for token in wallet.get("spl_tokens", []):
            mint = token["mint"]
            totals[mint] = totals.get(mint, Decimal(0)) + Decimal(token["raw_balance"]) / Decimal(10) ** token["decimals"]
        # The monitor's display list is bounded; do not infer omitted holdings as zero.
        if wallet.get("spl_token_count", 0) <= len(wallet.get("spl_tokens", [])):
            assets.update({mint: str(value) for mint, value in totals.items()})
            assets.update({mint: "0" for mint in tracker["assets"] if mint != "SOL" and mint not in totals})
            if tracker.get("token_baseline"):
                for mint in totals:
                    tracker["assets"].setdefault(mint, {"amount": "0", "at": now})
            tracker["token_baseline"] = True
    balance_changes = []
    for asset, amount in assets.items():
        previous = tracker["assets"].get(asset)
        if previous:
            delta = Decimal(amount) - Decimal(previous["amount"])
            if delta:
                event = {"asset": asset, "amount": str(delta), "observed_at": now, "evidence": "finalized_balance_snapshots", "attribution": "UNATTRIBUTED_BALANCE_CHANGE"}
                ident = hashlib.sha256(repr([asset, previous, amount]).encode()).hexdigest()[:32]
                tracker["events"][ident] = event
                balance_changes.append(event)
        tracker["assets"][asset] = {"amount": amount, "at": now}
    incoming, processed, errors = fetch(address, wallet.get("used_rpc", ""), set(tracker["signatures"]))
    for signature in processed:
        tracker["signatures"][signature] = now
    for event in incoming:
        ident = hashlib.sha256((event["signature"] + "\0" + event["asset"]).encode()).hexdigest()[:32]
        if Decimal(event["amount"]) > 0 and event["asset"] in config.get("trusted_mints", []):
            event["attribution"] = "CONFIRMED_RECEIVED_TOKEN"
        tracker["events"].setdefault(ident, event)
    # Attribution requires explicit reviewed provenance AND actual observed receipt.
    for expected in config.get("attributions", []):
        if not isinstance(expected, dict) or expected.get("reviewed") is not True or not str(expected.get("evidence_url", "")).startswith("https://"):
            continue
        quest = state.get("quest_queue", {}).get(expected.get("quest_id"))
        if not quest or not quest.get("evidence_verified"):
            continue
        for event in tracker["events"].values():
            if (event.get("signature") == expected.get("signature") and event["asset"] == expected.get("asset") and Decimal(event["amount"]) > 0):
                event.update(attribution="CONFIRMED_QUEST_REWARD", quest_id=expected["quest_id"], attribution_evidence=expected["evidence_url"])
    tracker["updated_at"] = now
    events = list(tracker["events"].values())
    return {
        "status": "DEGRADED" if errors else "OK", "balance_changes": balance_changes,
        "actual_rewards_received": [e for e in events if e["attribution"] == "CONFIRMED_QUEST_REWARD"],
        "confirmed_received_tokens": [e for e in events if e["attribution"] == "CONFIRMED_RECEIVED_TOKEN"],
        "unverified_incoming_tokens": [e for e in events if e["attribution"] == "UNVERIFIED_INCOMING"],
        "offchain_points": [{"quest_id": q["id"], "status": "advertised_not_earned", "earned": None} for q in state.get("quest_queue", {}).values() if q["reward_type"] == "OFFCHAIN_POINTS"],
        "speculative_rewards": [q["id"] for q in state.get("quest_queue", {}).values() if q["reward_status"] == "speculative"],
        "coverage": "Latest 20 finalized wallet-address signatures per scan; not complete history. Snapshot changes and transaction deltas are overlapping evidence, never summed as income.",
        "errors": errors,
    }
