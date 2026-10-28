"""
Telegram notification engine (V1.3).

Sends an Indonesian daily summary plus one block per newly discovered
opportunity. Each block answers the questions a reader actually needs:

* what the project and campaign are,
* what kind of opportunity it is (and why),
* where the official source is,
* what to do,
* what the reward status is (stated, points only, speculative, unknown),
* the deadline when the source gave a date,
* why it passes or fails the zero-capital filter,
* the risk score with a readable trail.

Messages are chunked to Telegram's limit, and failures are reported rather
than raised so the rest of the run still finishes. Bot tokens are never
logged and never written to a report.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Sequence

import requests

logger = logging.getLogger(__name__)

MESSAGE_MAX_LENGTH = 4000
CHUNK_TARGET_BYTES = 3800
REQUEST_TIMEOUT = 20
MAX_BLOCKS_PER_MESSAGE = 3

TYPE_LABELS = {
    "CONFIRMED_AIRDROP": "CONFIRMED_AIRDROP",
    "POINTS_PROGRAM": "POINTS_PROGRAM",
    "TESTNET_POTENTIAL": "TESTNET_POTENTIAL",
    "FREE_QUEST": "FREE_QUEST",
    "UNVERIFIED": "UNVERIFIED",
}

REWARD_LABELS = {
    "stated": "disebut sumber",
    "points_only": "hanya poin",
    "speculative": "spekulatif",
    "unspecified": "tidak disebutkan",
}


def _redact_token(token: Any) -> str:
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
    except requests.RequestException as exc:
        result["error"] = f"Telegram request failed: {type(exc).__name__}"
        logger.warning("Telegram notification failed: %s", result["error"])
        return result
    except Exception as exc:
        result["error"] = f"Unexpected: {type(exc).__name__}"
        logger.warning("Telegram notification failed: %s", result["error"])
        return result

    # A 4xx/5xx from Telegram usually still carries a useful description,
    # so read the body first instead of throwing it away.
    try:
        data = resp.json()
    except ValueError:
        result["error"] = f"Telegram HTTP {resp.status_code}: response was not JSON"
        logger.warning("Telegram notification failed: %s", result["error"])
        return result
    except Exception as exc:
        result["error"] = f"Unexpected: {type(exc).__name__}"
        return result

    if isinstance(data, dict):
        result["raw"] = _mask_response(data)
        if data.get("ok") is True:
            result["sent"] = True
        else:
            result["error"] = str(
                data.get("description") or data.get("error_code") or f"HTTP {resp.status_code}"
            )
    else:
        result["error"] = f"Telegram HTTP {resp.status_code}: unexpected payload"

    if not result["sent"]:
        logger.warning("Telegram notification failed: %s", result["error"])
    return result


def send_messages(
    bot_token: str,
    chat_id: str,
    messages: Sequence[str],
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """Send several messages and summarise the outcome.

    A failure in one message does not stop the others, but it is reported.
    """
    summary: Dict[str, Any] = {
        "sent": False,
        "messages": 0,
        "sent_count": 0,
        "bytes": 0,
        "error": None,
    }
    if not messages:
        summary["error"] = "Nothing to send"
        return summary

    errors: List[str] = []
    for text in messages:
        result = notify(bot_token, chat_id, text, timeout=timeout)
        summary["messages"] += 1
        summary["bytes"] += int(result.get("bytes") or 0)
        if result.get("sent"):
            summary["sent_count"] += 1
        else:
            errors.append(str(result.get("error") or "unknown error"))

    summary["sent"] = summary["sent_count"] == summary["messages"]
    if errors:
        summary["error"] = "; ".join(errors[:3])
    return summary


def _redact_safe_token(token: str) -> str:
    """The real token, used only to construct the request URL."""
    return str(token).strip()


def _trim_message(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MESSAGE_MAX_LENGTH:
        return text
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


def _as_mapping(item: Any) -> Dict[str, Any]:
    """Normalize a SourceHealth dataclass or plain dict into a dict."""
    if isinstance(item, dict):
        return item
    if hasattr(item, "__dict__"):
        return dict(vars(item))
    return {}


def _fmt_sol(wallet_summary: Dict[str, Any]) -> str:
    sol = wallet_summary.get("sol_balance")
    if sol is None:
        return "Saldo SOL: tidak tersedia"
    try:
        return f"Saldo SOL: {float(sol):,.6f} SOL"
    except (TypeError, ValueError):
        return "Saldo SOL: tidak tersedia"


def _fmt_tokens(wallet_summary: Dict[str, Any]) -> str:
    count = wallet_summary.get("spl_token_count")
    status = str(wallet_summary.get("spl_status") or "")
    if count is None or status == "unavailable":
        if wallet_summary.get("address_valid") is False:
            # Not an RPC failure: there was no valid address to query.
            diagnostics = str(wallet_summary.get("diagnostics") or "").lower()
            if "no address" in diagnostics:
                return "Aset SPL: tidak tersedia (alamat belum diatur)"
            if "invalid" in diagnostics:
                return "Aset SPL: tidak tersedia (alamat tidak valid)"
            return "Aset SPL: tidak tersedia (alamat tidak dapat dibaca)"
        return "Aset SPL: tidak tersedia (RPC gagal)"
    if count == 0:
        return "Aset SPL: 0 token (saldo kosong, RPC berhasil)"
    return f"Aset SPL: {count} token"


def _fmt_zero_capital(item: Dict[str, Any]) -> str:
    value = item.get("zero_capital_ok")
    reason = str(item.get("zero_capital_reason") or "").strip()
    if value is True:
        label = "YA - tanpa setoran/pembelian"
    elif value is False:
        label = "TIDAK - ada biaya/setoran"
    else:
        label = "BELUM JELAS - biaya belum disebut"
    return f"Zero-capital: {label}" + (f" ({reason})" if reason else "")


def _fmt_risk(item: Dict[str, Any]) -> List[str]:
    score = item.get("risk_score")
    band = str(item.get("risk_band") or "").strip()
    if score is None:
        return ["Risiko: tidak dihitung"]
    header = f"Risiko: {score}/100" + (f" ({band})" if band else "")
    lines = [header]
    for line in (item.get("risk_explanation") or [])[:3]:
        lines.append(f"  - {line}")
    return lines


def build_opportunity_block(campaign: Dict[str, Any], index: int) -> str:
    """One readable block for a single opportunity."""
    title = str(campaign.get("title") or "Tanpa judul")[:90]
    project = str(campaign.get("project") or "").strip()
    opp_type = str(campaign.get("opportunity_type") or "UNVERIFIED")
    reason = str(campaign.get("opportunity_reason") or "").strip()

    heading = f"{index}. {title}"
    if project and project.lower() not in title.lower():
        heading = f"{index}. {project} - {title}"

    lines: List[str] = [heading]
    lines.append(f"   Tipe: {TYPE_LABELS.get(opp_type, opp_type)}")
    if reason:
        lines.append(f"   Alasan: {reason}")

    official = str(campaign.get("official_url") or "").strip()
    if official:
        lines.append(f"   Sumber resmi: {official}")
    link = str(campaign.get("url") or "").strip()
    if link and link != official:
        lines.append(f"   Link: {link}")
    announcement = str(campaign.get("announcement_url") or "").strip()
    if announcement and announcement not in (link, official):
        lines.append(f"   Listing: {announcement}")
    if not official and not link:
        lines.append("   Link: tidak tersedia di sumber")

    tasks = campaign.get("tasks") or []
    if tasks:
        lines.append("   Aksi:")
        for task in tasks[:3]:
            lines.append(f"     - {str(task)[:150]}")
    else:
        lines.append("   Aksi: tidak dirinci di sumber")

    reward_status = str(campaign.get("reward_status") or "unspecified")
    reward_text = str(campaign.get("reward_text") or "").strip()
    reward_line = f"   Hadiah: {REWARD_LABELS.get(reward_status, reward_status)}"
    if reward_text:
        reward_line += f' - "{reward_text[:140]}"'
    lines.append(reward_line)
    reward_note = str(campaign.get("reward_note") or "").strip()
    if reward_note and reward_status in ("points_only", "speculative", "unspecified"):
        lines.append(f"     {reward_note}")

    deadline = str(campaign.get("deadline") or "").strip()
    if deadline:
        text = str(campaign.get("deadline_text") or "").strip()
        lines.append(f"   Deadline: {deadline}" + (f' ("{text[:110]}")' if text else ""))
    else:
        lines.append("   Deadline: tidak disebutkan sumber")

    lines.append("   " + _fmt_zero_capital(campaign))
    for line in _fmt_risk(campaign):
        lines.append("   " + line)

    note = str(campaign.get("cross_check_note") or "").strip()
    if note:
        lines.append(f"   Verifikasi: {note}")
    else:
        lines.append("   Verifikasi: hanya satu sumber pada scan ini")

    return "\n".join(lines)


def build_daily_summary(
    scan_time: str,
    wallet_summary: Dict[str, Any],
    campaigns: Sequence[Dict[str, Any]],
    source_health: Sequence[Any],
    errors: Sequence[str],
    total_found: Any = None,
    duplicates_skipped: int = 0,
) -> str:
    """A concise summary that is sent even when nothing new was found."""
    sources = [_as_mapping(s) for s in source_health]
    reachable = sum(1 for s in sources if s.get("reachable"))
    total_sources = len(sources)

    items = list(campaigns)
    counts: Dict[str, int] = {}
    for item in items:
        key = str(item.get("opportunity_type") or "UNVERIFIED")
        counts[key] = counts.get(key, 0) + 1

    zero_yes = sum(1 for i in items if i.get("zero_capital_ok") is True)
    zero_no = sum(1 for i in items if i.get("zero_capital_ok") is False)
    zero_unknown = sum(1 for i in items if i.get("zero_capital_ok") is None)

    new_count = len(items)
    total = total_found if total_found is not None else new_count

    lines: List[str] = []
    lines.append("LUMAVUN Crypto Agent V1.3 - ringkasan harian")
    lines.append(f"Scan: {scan_time}")
    lines.append("")
    lines.append(_fmt_sol(wallet_summary))
    lines.append(_fmt_tokens(wallet_summary))
    lines.append(
        f"Sumber: {reachable}/{total_sources if total_sources else 0} berhasil diakses"
    )
    lines.append(
        f"Kampanye ditemukan: {total} | baru: {new_count} | duplikat dilewati: {duplicates_skipped}"
    )

    if counts:
        parts = [
            f"{TYPE_LABELS.get(k, k)} {counts[k]}"
            for k in [
                "CONFIRMED_AIRDROP",
                "POINTS_PROGRAM",
                "TESTNET_POTENTIAL",
                "FREE_QUEST",
                "UNVERIFIED",
            ]
            if k in counts
        ]
        lines.append("Tipe: " + " | ".join(parts))
    else:
        lines.append("Tipe: tidak ada kampanye baru")

    lines.append(
        f"Zero-capital: {zero_yes} lolos | {zero_no} butuh biaya | {zero_unknown} belum jelas"
    )

    if new_count == 0:
        lines.append("")
        lines.append(
            "Tidak ada peluang baru pada scan ini. Ringkasan tetap dikirim agar "
            "kegagalan sumber atau wallet terlihat."
        )

    failed = [s for s in sources if not s.get("reachable")]
    if failed:
        lines.append("")
        lines.append("Sumber gagal:")
        for source in failed[:3]:
            url = str(source.get("url") or "tanpa url")[:80]
            reason = str(source.get("error") or "tidak diketahui")[:80]
            lines.append(f"- {url} ({reason})")

    if errors:
        lines.append("")
        lines.append("Catatan error:")
        for err in list(errors)[:3]:
            lines.append(f"- {str(err)[:150]}")

    lines.append("")
    lines.append(
        "Semua peluang wajib diverifikasi manual. Tidak ada klaim otomatis, "
        "tanda tangan transaksi, atau setoran."
    )
    return "\n".join(lines)


def chunk_message(text: str, max_bytes: int = CHUNK_TARGET_BYTES) -> List[str]:
    """Split a message at blank-line boundaries under a byte budget."""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_bytes = 0
    for block in text.split("\n\n"):
        block_bytes = len(block.encode("utf-8")) + 2
        if current and current_bytes + block_bytes > max_bytes:
            chunks.append("\n\n".join(current))
            current = [block]
            current_bytes = block_bytes
        else:
            current.append(block)
            current_bytes += block_bytes
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def build_messages(
    scan_time: str,
    wallet_summary: Dict[str, Any],
    campaigns: Sequence[Dict[str, Any]],
    source_health: Sequence[Any],
    errors: Sequence[str],
    total_found: Any = None,
    duplicates_skipped: int = 0,
    max_opportunities: int = 8,
) -> List[str]:
    """Build the daily summary plus opportunity blocks, chunked to fit."""
    summary = build_daily_summary(
        scan_time=scan_time,
        wallet_summary=wallet_summary,
        campaigns=campaigns,
        source_health=source_health,
        errors=errors,
        total_found=total_found,
        duplicates_skipped=duplicates_skipped,
    )
    messages: List[str] = [summary]

    items = list(campaigns)[:max_opportunities]
    if items:
        for start in range(0, len(items), MAX_BLOCKS_PER_MESSAGE):
            group = items[start : start + MAX_BLOCKS_PER_MESSAGE]
            blocks = [
                build_opportunity_block(item, index=start + offset + 1)
                for offset, item in enumerate(group)
            ]
            messages.append("\n\n".join(blocks))

    if len(campaigns) > len(items):
        messages.append(
            f"... dan {len(campaigns) - len(items)} peluang lain ada di laporan JSON/Markdown."
        )

    flattened: List[str] = []
    for message in messages:
        flattened.extend(chunk_message(message))
    return flattened


def build_report_message(
    scan_time: str,
    wallet_summary: Dict[str, Any],
    campaigns: Sequence[Dict[str, Any]],
    source_health: Sequence[Any],
    errors: Sequence[str],
) -> str:
    """Build a readable Indonesian notification message (legacy format).

    Kept for backward compatibility and for single-message callers; the
    richer output lives in :func:`build_messages`.
    """
    lines: List[str] = []
    lines.append("LUMAVUN Crypto Agent V1.3")
    lines.append("")
    lines.append(f"Waktu scan: {scan_time}")
    lines.append("")

    lines.append(_fmt_sol(wallet_summary))

    token_count = wallet_summary.get("spl_token_count")
    if token_count:
        lines.append(f"Jumlah aset SPL token: {token_count}")
    else:
        lines.append("Jumlah aset SPL token: tidak tersedia / belum ada")
    lines.append("")

    sources = [_as_mapping(s) for s in source_health]
    reachable = sum(1 for s in sources if s.get("reachable"))
    total_sources = len(sources) or 1
    lines.append(f"Sumber feed: {reachable}/{total_sources} berhasil diakses")
    lines.append("")

    lines.append(f"Kampanye terdeteksi: {len(campaigns)}")
    if campaigns:
        lines.append("")
        for item in campaigns[:8]:
            title = str(item.get("title", "Tanpa judul"))[:80]
            url = item.get("url", "")
            source = item.get("source", "")
            flags = item.get("risk_flags", [])
            classification = item.get("classification", "REVIEW_REQUIRED")
            conf = "Konfirmasi teks" if item.get("is_confirmed") else ""
            spec = "Spekulasi" if item.get("is_speculation") else ""
            flag_text = ", ".join(flags) if flags else "-"
            lines.append(f"- {title}")
            link_line = f"  Sumber: {source}"
            if url:
                link_line += f" | {url}"
            lines.append(link_line)
            lines.append(f"  Klasifikasi: {classification}")
            if item.get("opportunity_type"):
                lines.append(f"  Tipe peluang: {item.get('opportunity_type')}")
            lines.append(f"  Indikator: {flag_text} {conf} {spec}".rstrip())
            lines.append("")
    else:
        lines.append("Tidak ada kampanye yang sesuai dengan kata kunci.")
        lines.append("")

    if errors:
        lines.append("Catatan error:")
        for err in errors[:5]:
            lines.append(f"- {str(err)[:160]}")
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
