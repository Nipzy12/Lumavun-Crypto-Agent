#!/usr/bin/env python3
"""Check whether a Solana public address is valid before setting it as a
GitHub Actions variable.

Usage:
    python scripts/check_address.py <address> [<address> ...]
    python scripts/check_address.py --env PHANTOM_PUBLIC_ADDRESS

A valid Solana address is Base58 text that decodes to exactly 32 bytes.
Surrounding whitespace is ignored. Nothing is sent anywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wallet import validate_solana_address  # noqa: E402

BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def explain(address: str) -> str:
    """Return a human-readable reason for a pass or failure."""
    raw = (address or "").strip()
    if not raw:
        return "empty value"
    bad = sorted({ch for ch in raw if ch not in BASE58_ALPHABET})
    if bad:
        shown = " ".join(repr(ch) for ch in bad)
        return (
            f"contains characters outside the Base58 alphabet: {shown}. "
            "Solana addresses never contain 0, O, I, or l."
        )
    if len(raw) < 32 or len(raw) > 44:
        return f"length {len(raw)} is outside the 32-44 character range"
    try:
        import base58

        decoded = base58.b58decode(raw)
    except Exception as exc:  # pragma: no cover - defensive
        return f"Base58 decode failed: {type(exc).__name__}"
    if len(decoded) != 32:
        return f"decodes to {len(decoded)} bytes; a Solana public key must be 32"
    return "ok"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2

    if argv[0] == "--env":
        name = argv[1] if len(argv) > 1 else "PHANTOM_PUBLIC_ADDRESS"
        value = os.getenv(name, "")
        if not value:
            print(f"{name} is not set in this environment")
            return 2
        targets = [(name, value)]
    else:
        targets = [(a, a) for a in argv]

    failures = 0
    for label, address in targets:
        ok = validate_solana_address(address)
        reason = explain(address)
        status = "VALID" if ok else "INVALID"
        print(f"{status:8s} {address or '(empty)'}")
        if not ok:
            print(f"         reason: {reason}")
            failures += 1
        elif label != address:
            print(f"         source: {label}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
