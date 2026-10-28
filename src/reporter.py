"""
Report generation.

Produces machine-readable and human-readable reports. Missing data is
reported as absent, not as zero.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from discovery import Campaign, SourceHealth


def build_report(
    scan_time: str,
    campaigns: Sequence[Campaign],
    source_health: Sequence[SourceHealth],
    wallet: Dict[str, Any],
    errors: Sequence[str],
    telegram_result: Optional[Dict[str, Any]] = None,
    duplicates_skipped: int = 0,
) -> Dict[str, Any]:
    """Build the machine-readable report dict."""
    return {
        "agent": "LUMAVUN Crypto Agent V1.1",
        "scan_time_utc": scan_time,
        "wallet": wallet,
        "source_health": [
            {
                "url": h.url,
                "reachable": h.reachable,
                "error": h.error,
                "entries_analyzed": h.entries_analyzed,
                "campaigns_found": h.campaigns_found,
            }
            for h in source_health
        ],
        "campaigns": [
            {
                "title": c.title,
                "project": c.project,
                "url": c.url,
                "source": c.source,
                "published": c.published,
                "requirements": c.requirements,
                "risk_flags": c.risk_flags,
                "classification": c.classification,
                "is_confirmed": c.is_confirmed,
                "is_speculation": c.is_speculation,
            }
            for c in campaigns
        ],
        "classification_summary": _classification_summary(campaigns),
        "errors": list(errors),
        "telegram": _mask_telegram_result(telegram_result),
        "duplicates_skipped": duplicates_skipped,
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


def _mask_telegram_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "sent": bool(result.get("sent")),
        "bytes": result.get("bytes"),
        "error": result.get("error"),
    }


def write_json_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = path or Path("latest_report.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return target


def write_markdown_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = path or Path("latest_report.md")
    lines: List[str] = []
    lines.append("# LUMAVUN Crypto Agent V1.1 — Laporan Scan")
    lines.append("")
    lines.append(f"- **Waktu scan (UTC):** {report.get('scan_time_utc', 'tidak tersedia')}")
    lines.append("")

    wallet = report.get("wallet", {})
    sol = wallet.get("sol_balance")
    if sol is None:
        lines.append("- **Saldo SOL:** tidak tersedia")
    else:
        lines.append(f"- **Saldo SOL:** {sol:,.6f} SOL")
    lines.append(f"- **Jumlah aset SPL token:** {wallet.get('spl_token_count', 0) or 'tidak tersedia'}")
    lines.append("")

    lines.append("## Kesehatan sumber")
    lines.append("")
    for h in report.get("source_health", []):
        status = "tersedia" if h.get("reachable") else "tidak tersedia"
        lines.append(f"- {h.get('url', 'tanpa url')} — {status}")
        if h.get("error"):
            lines.append(f"  - error: {h['error']}")
        lines.append(f"  - entri dipindai: {h.get('entries_analyzed', 0)}")
        lines.append(f"  - kampanye ditemukan: {h.get('campaigns_found', 0)}")
    lines.append("")

    lines.append("## Kampanye terdeteksi")
    lines.append("")
    for i, c in enumerate(report.get("campaigns", []), start=1):
        lines.append(f"### {i}. {c.get('title', 'Tanpa judul')}")
        lines.append("")
        lines.append(f"- **Sumber:** {c.get('source', 'tanpa sumber')}")
        if c.get("url"):
            lines.append(f"- **Link:** {c.get('url')}")
        if c.get("project"):
            lines.append(f"- **Proyek:** {c.get('project')}")
        if c.get("published"):
            lines.append(f"- **Tanggal publikasi:** {c.get('published')}")
        lines.append(f"- **Klasifikasi:** {c.get('classification', 'REVIEW_REQUIRED')}")
        lines.append(f"- **Indikator risiko:** {', '.join(c.get('risk_flags', [])) or '-'}")
        if c.get("requirements"):
            lines.append("- **Persyaratan:**")
            for req in c.get("requirements", []):
                lines.append(f"  - {req}")
        lines.append("")
        note = []
        if c.get("is_confirmed"):
            note.append("Teks feed menunjukkan sinyal konfirmasi kampanye.")
        if c.get("is_speculation"):
            note.append("Teks feed mengandung sinyal spekulasi.")
        if note:
            lines.append(f"> Catatan: {' '.join(note)}")
            lines.append("")
    lines.append("")

    lines.append("## Ringkasan klasifikasi")
    lines.append("")
    summary = report.get("classification_summary", {})
    for label, count in summary.items():
        lines.append(f"- **{label}:** {count}")
    lines.append("")

    lines.append("## Persyaratan umum")
    lines.append("")
    lines.append(
        "Klasifikasi `LOW_RISK_INDICATORS` hanya berarti lebih sedikit indikator "
        "risiko heuristik yang ditemukan dalam teks feed. Status ini **tidak** "
        "menjamin keamanan. Selalu verifikasi secara manual sebelum berinteraksi "
        "dengan wallet, permintaan tanda tangan, atau setoran apa pun."
    )
    lines.append("")

    if report.get("errors"):
        lines.append("## Catatan error")
        lines.append("")
        for err in report.get("errors", []):
            lines.append(f"- {err[:200]}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*LUMAVUN Crypto Agent V1.1 — baca-only, tanpa klaim, tanda tangan, atau setoran otomatis.*")

    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return target
