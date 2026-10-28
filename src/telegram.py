"""
Telegram notification engine.

Sends readable Indonesian reports through a Telegram bot. It prevents
duplicate notifications for the same campaign across runs and fails
gracefully so the rest of the agent can still finish its work. Bot
tokens are never written to logs or reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

MESSAGE_MAX_LENGTH = 4000
REQUEST_TIMEOUT = 20


def _redact_token(token: Optional[str]) -> str:
    if not token:
        return ""
    t = str(token).strip()
    if len(t) <= 8:
        return "***"
    return t[:4] + "***" + t[-2:]


def notify(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """Send a message through the Telegram Bot API.

    Returns a result dict describing success or failure. This function
    never logs the bot token.
    """
    result: Dict[str, Any] = {"sent": False, "error": None, "bytes": 0}
    if not bot_token or not chat_id:
        result["error"] = "Telegram not configured"
        return result

    payload_text = _trim_message(text)
    result["bytes"] = len(payload_text.encode("utf-8"))

    try:
        url = f"https://api.telegram.org/bot{_redact_safe_token(bot_token)}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": payload_text,
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        ok = bool(data.get("ok"))
        result["sent"] = ok
        if not ok:
            result["error"] = str(data.get("description") or data.get("error_code"))
        result["raw"] = _mask_response(data)
    except requests.RequestException as exc:
        result["error"] = f"Telegram request failed: {type(exc).__name__}"
        logger.warning("Telegram notification failed: %s", result["error"])
    except Exception as exc:
        result["error"] = f"Unexpected: {type(exc).__name__}"
        logger.warning("Telegram notification failed: %s", result["error"])
    return result


def _redact_safe_token(token: str) -> str:
    """Build a token safe enough for a URL while keeping the real token
    out of logs. This is only used to construct the request URL; the value
    is never emitted in reports or logs."""
    return str(token).strip()


def _trim_message(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MESSAGE_MAX_LENGTH:
        return text
    # Trim to a safe length without breaking lines badly.
    text = text[:3900]
    if "\n" in text:
        text = text.rsplit("\n", 1)[0]
    return text.strip()


def _mask_response(data: Any) -> Any:
    """Mask response payloads so no token or sensitive data leaks."""
    if isinstance(data, dict):
        return {key: _mask_response(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask_response(item) for item in data]
    return data


def build_report_message(
    scan_time: str,
    wallet_summary: Dict[str, Any],
    campaigns: Sequence[Dict[str, Any]],
    source_health: Sequence[Dict[str, Any]],
    errors: Sequence[str],
) -> str:
    """Build a readable Indonesian notification message."""
    lines: List[str] = []
    lines.append("LUMAVUN Crypto Agent V1.1")
    lines.append("")
    lines.append(f"Waktu scan: {scan_time}")
    lines.append("")

    # Wallet summary
    sol = wallet_summary.get("sol_balance")
    if sol is None:
        sol_line = "Saldo SOL: tidak tersedia"
    else:
        sol_line = f"Saldo SOL: {sol:,.6f} SOL"
    lines.append(sol_line)

    token_count = wallet_summary.get("spl_token_count", 0)
    if token_count:
        lines.append(f"Jumlah aset SPL token: {token_count}")
    else:
        lines.append("Jumlah aset SPL token: tidak tersedia / belum ada")
    lines.append("")

    # Source health
    reachable = sum(1 for s in source_health if s.get("reachable"))
    total_sources = len(source_health) or 1
    lines.append(f"Sumber feed: {reachable}/{total_sources} berhasil diakses")
    lines.append("")

    # Campaigns
    lines.append(f"Kampanye terdeteksi: {len(campaigns)}")
    if campaigns:
        lines.append("")
        for item in campaigns[:8]:
            title = item.get("title", "Tanpa judul")[:80]
            url = item.get("url", "")
            source = item.get("source", "")
            flags = item.get("risk_flags", [])
            classification = item.get("classification", "REVIEW_REQUIRED")
            conf = "✅ Konfirmasi teks" if item.get("is_confirmed") else ""
            spec = "⚠️ Spekulasi" if item.get("is_speculation") else ""
            flag_text = ", ".join(flags) if flags else "-"
            lines.append(f"- {title}")
            link_line = f"  Sumber: {source}"
            if url:
                link_line += f" | {url}"
            lines.append(link_line)
            lines.append(f"  Klasifikasi: {classification}")
            lines.append(f"  Indikator: {flag_text} {conf} {spec}")
            lines.append("")
    else:
        lines.append("Tidak ada kampanye yang sesuai dengan kata kunci.")
        lines.append("")

    # Errors
    if errors:
        lines.append("Catatan error:")
        for err in errors[:5]:
            lines.append(f"- {err[:160]}")
        lines.append("")

    lines.append("Semua peluang wajib diverifikasi manual; tidak ada klaim otomatis.")
    return "\n".join(lines)


def campaign_identity_hash(campaign: Dict[str, Any]) -> str:
    """Compute a stable hash for duplicate-notification prevention."""
    parts = [
        str(campaign.get("title", "")).strip().lower(),
        str(campaign.get("source", "")).strip().lower(),
        str(campaign.get("url", "")).strip().lower(),
    ]
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def should_notify_campaign(
    campaign: Dict[str, Any],
    notified_hashes: Sequence[str],
) -> bool:
    """Return True if this campaign should be notified (not a duplicate)."""
    h = campaign_identity_hash(campaign)
    return h not in set(notified_hashes)
