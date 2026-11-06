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

AGENT_VERSION = "1.4.3"
OPPORTUNITY_TYPES = (
    "CONFIRMED_AIRDROP",
    "POINTS_PROGRAM",
    "TESTNET_POTENTIAL",
    "FREE_QUEST",
    "UNVERIFIED",
    "NEWS_ONLY",
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
        "cost_status": c.cost_status,
        "evidence_verified": c.evidence_verified,
        "source_verification_status": c.source_verification_status,
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
    quest_queue: Optional[Dict[str, Any]] = None,
    adapter_health: Optional[List[Dict[str, Any]]] = None,
    reward_tracking: Optional[Dict[str, Any]] = None,
    recovery_report: Optional[Dict[str, Any]] = None,
    discovery_diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the machine-readable report dict."""
    campaign_dicts = [_campaign_dict(c) for c in campaigns]
    return {
        "agent": f"LUMAVUN Crypto Agent V{AGENT_VERSION}",
        "quest_queue": quest_queue or {},
        "discovery_diagnostics": discovery_diagnostics or {},
        "quest_adapters": adapter_health or [],
        "reward_tracking": reward_tracking or {},
        "opportunity_recovery": recovery_report or {},
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
                "entries_received": h.entries_received,
                "campaigns_found": h.campaigns_found,
                "source_id": h.source_id,
                "label": h.label,
                "category": h.category,
                "official": h.official,
                "http_status": h.http_status,
                "content_type": h.content_type,
                "robots_blocked": h.robots_blocked,
                "rejected_news": h.rejected_news,
                "verified_opportunities": h.verified_opportunities,
            }
            for h in source_health
        ],
        "source_summary": {
            "total": len(source_health),
            "reachable": sum(1 for h in source_health if h.reachable),
            "official_sources": sum(1 for h in source_health if h.official),
        },
        "rejected_news_count": sum(h.rejected_news for h in source_health),
        "verified_opportunities": sum(h.verified_opportunities for h in source_health),
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

    lines.extend(["## Diagnostik discovery", ""])
    diagnostics = report.get("discovery_diagnostics") or {}
    for metric, value in diagnostics.get("statistics", {}).items():
        lines.append(f"- {metric}: {value}")
    for row in diagnostics.get("candidates", []):
        lines.append(f"- {row['name']} ({row['cost_status']}): " + ", ".join(r["code"] for r in row["reasons"]))
        lines.append(f"  - sumber: {row['source_url']}; langkah berikut: " + "; ".join(row["next_actions"]))
    for failure in diagnostics.get("source_failures", []):
        lines.append(f"- SOURCE_UNREACHABLE: {failure['source_url']}")
    lines.append("")
    lines.extend(["## Antrean quest V1.4", ""])
    for quest in (report.get("quest_queue") or {}).values():
        lines.append(f"- **{quest['name']}** — {quest['status']} | biaya {quest['cost_status']} | eligibility {quest['eligibility_status']}")
        lines.append(f"  - {quest.get('official_url') or 'URL resmi belum terverifikasi'} | deadline {quest.get('end_at') or 'tidak disebutkan'}")
        qualification = quest.get("qualification", {})
        lines.append(f"  - Qualification: {qualification.get('status', 'REVIEW_REQUIRED')} | score {qualification.get('score', 0)} | reward {qualification.get('reward_class', 'UNKNOWN_REWARD')}")
        for name, evidence in qualification.get("fields", {}).items():
            lines.append(f"    - {name}: {evidence['status']} — {evidence['explanation']} | {evidence['evidence_url']} | checked {evidence['last_verified_at']}")
        for task in quest.get("tasks", {}).values():
            lines.append(f"  - [{task['status']}] {task['text']}")
    recovery = report.get("opportunity_recovery") or {}
    lines.extend(["", "## Evidence recovery", ""])
    lines.append(f"Total persisted candidates: {recovery.get('total_candidates', 0)} | {recovery.get('qualification_counts', {})}")
    lines.append(f"All public evidence resolved: {recovery.get('all_public_evidence_resolved', 0)}; participant-specific checks pending: {recovery.get('awaiting_participant_verification', 0)}")
    for field, group in recovery.get('review_required_by_missing_field', {}).items():
        lines.append(f"- {field}: {group['count']}")
    lines.append("Complete field gaps and resolution outcomes: opportunity_recovery.json. Badges/points are not income.")
    lines.extend(["", "## Integrasi quest", ""])
    for adapter in report.get("quest_adapters") or []:
        lines.append(f"- {adapter['adapter']}: {adapter['status']} — {adapter['reason']}")
    rewards = report.get("reward_tracking") or {}
    lines.extend(["", "## Pelacakan reward read-only", ""])
    lines.append(f"Status: {rewards.get('status', 'UNAVAILABLE')}; reward diterima dengan atribusi: {len(rewards.get('actual_rewards_received', []))}.")
    lines.append("Token masuk, perubahan saldo, poin yang dijanjikan, dan reward terverifikasi dilaporkan terpisah; tidak ada estimasi USD.")
    for event in rewards.get("actual_rewards_received", []):
        lines.append(f"- {event['amount']} {event['asset']} | tx {event.get('signature', '-')} | quest {event.get('quest_id', '-')}")
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
