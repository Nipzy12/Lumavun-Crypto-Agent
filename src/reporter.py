"""
Report generation (V1.3).

Produces machine-readable and human-readable reports. Missing data is
reported as absent, not as zero, and every heuristic is labelled as such so
a reader can tell a quoted fact from an interpretation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from discovery import Campaign, SourceHealth

AGENT_VERSION = "1.3"
OPPORTUNITY_TYPES = (
    "CONFIRMED_AIRDROP",
    "POINTS_PROGRAM",
    "TESTNET_POTENTIAL",
    "FREE_QUEST",
    "UNVERIFIED",
)


def _campaign_dict(c: Campaign) -> Dict[str, Any]:
    return {
        # --- quoted from the source -------------------------------------
        "title": c.title,
        "project": c.project,
        "url": c.url,
        "source": c.source,
        "source_id": c.source_id,
        "source_label": c.source_label,
        "source_category": c.source_category,
        "source_is_official": c.source_is_official,
        "announcement_url": c.announcement_url,
        "published": c.published,
        "requirements": list(c.requirements),
        "raw_summary": c.raw_summary,
        "eligibility": list(c.eligibility),
        "tasks": list(c.tasks),
        "reward_status": c.reward_status,
        "reward_text": c.reward_text,
        "reward_note": c.reward_note,
        "deadline": c.deadline,
        "deadline_text": c.deadline_text,
        # --- labelled heuristics ----------------------------------------
        "campaign_id": c.campaign_id,
        "opportunity_type": c.opportunity_type,
        "opportunity_reason": c.opportunity_reason,
        "official_url": c.official_url,
        "zero_capital_ok": c.zero_capital_ok,
        "zero_capital_reason": c.zero_capital_reason,
        "zero_capital_blockers": list(c.zero_capital_blockers),
        "unknown_costs": list(c.unknown_costs),
        "risk_flags": list(c.risk_flags),
        "risk_score": c.risk_score,
        "risk_band": c.risk_band,
        "risk_explanation": list(c.risk_explanation),
        "classification": c.classification,
        "is_confirmed": c.is_confirmed,
        "is_speculation": c.is_speculation,
        "cross_checked": c.cross_checked,
        "cross_check_note": c.cross_check_note,
    }


def build_report(
    scan_time: str,
    campaigns: Sequence[Campaign],
    source_health: Sequence[SourceHealth],
    wallet: Dict[str, Any],
    errors: Sequence[str],
    telegram_result: Optional[Dict[str, Any]] = None,
    duplicates_skipped: int = 0,
    total_found: Optional[int] = None,
    state_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the machine-readable report dict."""
    campaign_dicts = [_campaign_dict(c) for c in campaigns]
    return {
        "agent": f"LUMAVUN Crypto Agent V{AGENT_VERSION}",
        "version": AGENT_VERSION,
        "scan_time_utc": scan_time,
        "state_source": state_source,
        "wallet": wallet,
        "source_health": [
            {
                "url": h.url,
                "reachable": h.reachable,
                "error": h.error,
                "entries_analyzed": h.entries_analyzed,
                "campaigns_found": h.campaigns_found,
                "source_id": h.source_id,
                "label": h.label,
                "category": h.category,
                "official": h.official,
                "http_status": h.http_status,
                "content_type": h.content_type,
                "robots_blocked": h.robots_blocked,
            }
            for h in source_health
        ],
        "source_summary": {
            "total": len(source_health),
            "reachable": sum(1 for h in source_health if h.reachable),
            "official_sources": sum(1 for h in source_health if h.official),
        },
        "campaigns": campaign_dicts,
        "campaign_count": len(campaign_dicts),
        "total_found": total_found if total_found is not None else len(campaign_dicts),
        "classification_summary": _classification_summary(campaigns),
        "opportunity_summary": _opportunity_summary(campaign_dicts),
        "zero_capital_summary": _zero_capital_summary(campaign_dicts),
        "errors": list(errors),
        "telegram": _mask_telegram_result(telegram_result),
        "duplicates_skipped": duplicates_skipped,
        "disclaimer": (
            "Klasifikasi tipe, skor risiko, dan filter zero-capital adalah "
            "heuristik yang dapat salah. Selalu verifikasi manual."
        ),
    }


def _classification_summary(campaigns: Sequence[Campaign]) -> Dict[str, int]:
    counts: Dict[str, int] = {
        "LOW_RISK_INDICATORS": 0,
        "REVIEW_REQUIRED": 0,
        "HIGH_RISK_INDICATORS": 0,
    }
    for c in campaigns:
        counts[c.classification] = counts.get(c.classification, 0) + 1
    return counts


def _opportunity_summary(campaign_dicts: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {name: 0 for name in OPPORTUNITY_TYPES}
    for c in campaign_dicts:
        key = str(c.get("opportunity_type") or "UNVERIFIED")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _zero_capital_summary(campaign_dicts: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "passes": sum(1 for c in campaign_dicts if c.get("zero_capital_ok") is True),
        "blocked": sum(1 for c in campaign_dicts if c.get("zero_capital_ok") is False),
        "unknown": sum(1 for c in campaign_dicts if c.get("zero_capital_ok") is None),
    }


def _mask_telegram_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "sent": bool(result.get("sent")),
        "messages": result.get("messages"),
        "sent_count": result.get("sent_count"),
        "bytes": result.get("bytes"),
        "error": result.get("error"),
    }


def write_json_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = path or Path("latest_report.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return target


def _yes_no(value: Any, unknown: str = "belum jelas") -> str:
    if value is True:
        return "ya"
    if value is False:
        return "tidak"
    return unknown


def write_markdown_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = path or Path("latest_report.md")
    lines: List[str] = []
    lines.append(f"# LUMAVUN Crypto Agent V{AGENT_VERSION} - Laporan Scan")
    lines.append("")
    lines.append(f"- **Waktu scan (UTC):** {report.get('scan_time_utc', 'tidak tersedia')}")
    if report.get("state_source"):
        lines.append(f"- **Sumber state dedup:** `{report['state_source']}`")
    lines.append(f"- **Duplikat dilewati:** {report.get('duplicates_skipped', 0)}")
    lines.append("")

    wallet = report.get("wallet", {}) or {}
    sol = wallet.get("sol_balance")
    if sol is None:
        lines.append("- **Saldo SOL:** tidak tersedia")
    else:
        lines.append(f"- **Saldo SOL:** {sol:,.6f} SOL")
    token_count = wallet.get("spl_token_count")
    if token_count is None or wallet.get("spl_status") == "unavailable":
        # Distinguish "the RPC failed" from "there was no address to read".
        if wallet.get("address_valid") is False:
            diagnostics = str(wallet.get("diagnostics") or "").lower()
            if "no address" in diagnostics:
                reason = "alamat belum diatur"
            elif "invalid" in diagnostics:
                reason = "alamat tidak valid"
            else:
                reason = "alamat tidak dapat dibaca"
            lines.append(f"- **Jumlah aset SPL token:** tidak tersedia ({reason})")
        else:
            lines.append("- **Jumlah aset SPL token:** tidak tersedia (RPC gagal)")
    else:
        lines.append(f"- **Jumlah aset SPL token:** {token_count}")
    if wallet.get("diagnostics"):
        lines.append(f"- **Diagnostik wallet:** {wallet['diagnostics']}")
    lines.append("")

    summary = report.get("source_summary", {}) or {}
    lines.append("## Kesehatan sumber")
    lines.append("")
    lines.append(
        f"{summary.get('reachable', 0)}/{summary.get('total', 0)} sumber berhasil diakses."
    )
    lines.append("")
    for h in report.get("source_health", []):
        status = "tersedia" if h.get("reachable") else "tidak tersedia"
        official = " (resmi)" if h.get("official") else ""
        label = h.get("label") or h.get("source_id") or ""
        lines.append(f"- {h.get('url', 'tanpa url')} - {status}{official}")
        if label:
            lines.append(f"  - kategori: {h.get('category', '-')} | {label}")
        if h.get("error"):
            lines.append(f"  - error: {h['error']}")
        lines.append(f"  - entri dipindai: {h.get('entries_analyzed', 0)}")
        lines.append(f"  - kampanye ditemukan: {h.get('campaigns_found', 0)}")
    lines.append("")

    lines.append("## Ringkasan tipe peluang")
    lines.append("")
    for label, count in (report.get("opportunity_summary", {}) or {}).items():
        lines.append(f"- **{label}:** {count}")
    lines.append("")

    zc = report.get("zero_capital_summary", {}) or {}
    lines.append("## Filter zero-capital")
    lines.append("")
    lines.append(f"- **Lolos:** {zc.get('passes', 0)}")
    lines.append(f"- **Terblokir biaya:** {zc.get('blocked', 0)}")
    lines.append(f"- **Belum jelas:** {zc.get('unknown', 0)}")
    lines.append("")

    lines.append("## Kampanye terdeteksi")
    lines.append("")
    campaigns = report.get("campaigns", []) or []
    if not campaigns:
        lines.append(
            "Tidak ada kampanye baru pada scan ini. Sumber tetap dipindai; "
            "lihat bagian kesehatan sumber di atas."
        )
        lines.append("")
    for i, c in enumerate(campaigns, start=1):
        lines.append(f"### {i}. {c.get('title', 'Tanpa judul')}")
        lines.append("")
        lines.append(f"- **Proyek:** {c.get('project') or 'tidak disebutkan'}")
        lines.append(f"- **Tipe peluang:** {c.get('opportunity_type', 'UNVERIFIED')}")
        if c.get("opportunity_reason"):
            lines.append(f"  - alasan: {c['opportunity_reason']}")
        lines.append(f"- **Sumber:** {c.get('source', 'tanpa sumber')}")
        if c.get("source_label"):
            lines.append(
                f"  - {c.get('source_label')} (kategori: {c.get('source_category', '-')})"
            )
        if c.get("official_url"):
            lines.append(f"- **Sumber resmi:** {c.get('official_url')}")
        if c.get("url"):
            lines.append(f"- **Link:** {c.get('url')}")
        if c.get("announcement_url") and c.get("announcement_url") not in (
            c.get("url"),
            c.get("official_url"),
        ):
            lines.append(f"- **Halaman pengumuman:** {c.get('announcement_url')}")
        if c.get("published"):
            lines.append(f"- **Tanggal publikasi:** {c.get('published')}")
        if c.get("deadline"):
            lines.append(f"- **Deadline:** {c.get('deadline')}")
            if c.get("deadline_text"):
                lines.append(f"  - kutipan: {c['deadline_text']}")
        else:
            lines.append("- **Deadline:** tidak disebutkan sumber")

        if c.get("tasks"):
            lines.append("- **Aksi yang diminta:**")
            for task in c["tasks"]:
                lines.append(f"  - {task}")
        else:
            lines.append("- **Aksi yang diminta:** tidak dirinci sumber")

        if c.get("eligibility"):
            lines.append("- **Eligibilitas (kutipan):**")
            for item in c["eligibility"]:
                lines.append(f"  - {item}")

        lines.append(f"- **Status hadiah:** {c.get('reward_status', 'unspecified')}")
        if c.get("reward_text"):
            lines.append(f"  - kutipan: {c['reward_text']}")
        if c.get("reward_note"):
            lines.append(f"  - catatan: {c['reward_note']}")

        lines.append(
            f"- **Zero-capital:** {_yes_no(c.get('zero_capital_ok'))}"
            + (f" - {c['zero_capital_reason']}" if c.get("zero_capital_reason") else "")
        )
        if c.get("zero_capital_blockers"):
            lines.append(f"  - penghalang: {', '.join(c['zero_capital_blockers'])}")
        if c.get("unknown_costs"):
            lines.append(f"  - biaya belum jelas: {', '.join(c['unknown_costs'])}")

        lines.append(
            f"- **Risiko:** {c.get('risk_score', 0)}/100 ({c.get('risk_band', '-')}) | "
            f"tingkat: {c.get('classification', 'REVIEW_REQUIRED')}"
        )
        for line in (c.get("risk_explanation") or [])[:5]:
            lines.append(f"  - {line}")

        lines.append(
            f"- **Verifikasi silang:** {'ya' if c.get('cross_checked') else 'belum'}"
            + (f" - {c['cross_check_note']}" if c.get("cross_check_note") else "")
        )
        lines.append("")

    lines.append("## Persyaratan umum")
    lines.append("")
    lines.append(
        "Klasifikasi `LOW_RISK_INDICATORS` dan skor risiko rendah hanya berarti "
        "lebih sedikit indikator risiko heuristik yang ditemukan dalam teks sumber. "
        "Status ini **tidak** menjamin keamanan. Kategori `POINTS_PROGRAM` tidak "
        "berarti hadiah token, dan `TESTNET_POTENTIAL` tidak berarti airdrop pasti. "
        "Selalu verifikasi manual sebelum berinteraksi dengan wallet, permintaan "
        "tanda tangan, atau setoran apa pun."
    )
    lines.append("")

    if report.get("errors"):
        lines.append("## Catatan error")
        lines.append("")
        for err in report.get("errors", []):
            lines.append(f"- {str(err)[:200]}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*LUMAVUN Crypto Agent V{AGENT_VERSION} - baca-only, tanpa klaim, "
        "tanda tangan, atau setoran otomatis.*"
    )

    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return target
