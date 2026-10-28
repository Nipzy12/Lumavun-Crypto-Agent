"""
Airdrop discovery engine (V1.3).

Reads every configured source (RSS/Atom feeds and public JSON APIs), keeps
only entries that look like a campaign, and turns each one into a
:class:`Campaign` carrying both the quoted evidence and the labelled
heuristics derived from it.

The engine never invents a campaign, project, deadline, task or reward. If
the payload does not contain a field, the field stays empty and the report
says so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import feedparser
import requests

from analysis import (
    classify_opportunity,
    cross_check_campaigns,
    domain_consistency,
    evaluate_zero_capital,
    extract_deadline,
    extract_eligibility,
    extract_reward,
    extract_tasks,
    normalize,
    project_key,
    score_risk,
)
from config_loader import load_config
from risk import risk_tier
from sources import (
    DEFAULT_MIN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    RawItem,
    SourceResult,
    SourceSpec,
    fetch_source,
    specs_from_urls,
    validate_source_url,
)

MAX_FEEDS = 10
MAX_ENTRIES_PER_FEED = 15
MAX_FINDINGS = 20
REQUEST_TIMEOUT = DEFAULT_TIMEOUT

USER_AGENT = DEFAULT_USER_AGENT


@dataclass
class Campaign:
    """A single extracted campaign candidate."""

    title: str
    project: str
    url: str
    source: str
    published: Optional[str] = None
    requirements: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    # Risk tier: LOW_RISK_INDICATORS / REVIEW_REQUIRED / HIGH_RISK_INDICATORS.
    classification: str = "REVIEW_REQUIRED"
    is_confirmed: bool = False
    is_speculation: bool = False
    raw_summary: str = ""

    # --- V1.3 -------------------------------------------------------------
    campaign_id: str = ""
    source_id: str = ""
    source_label: str = ""
    source_category: str = ""
    source_is_official: bool = False
    announcement_url: str = ""
    official_url: str = ""
    opportunity_type: str = "UNVERIFIED"
    opportunity_reason: str = ""
    deadline: Optional[str] = None
    deadline_text: Optional[str] = None
    eligibility: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)
    reward_status: str = "unspecified"
    reward_text: Optional[str] = None
    reward_note: str = ""
    zero_capital_ok: Optional[bool] = None
    zero_capital_reason: str = ""
    zero_capital_blockers: List[str] = field(default_factory=list)
    unknown_costs: List[str] = field(default_factory=list)
    risk_score: int = 0
    risk_band: str = "low"
    risk_explanation: List[str] = field(default_factory=list)
    cross_checked: bool = False
    cross_check_note: str = ""
    published_ts: Optional[float] = None

    def __post_init__(self) -> None:
        # Every campaign identifies itself the moment it exists, so reports,
        # state records and notification history all agree on one key.
        if not self.campaign_id:
            self.campaign_id = campaign_id_for(self)


@dataclass
class SourceHealth:
    """Per-source health information for the report."""

    url: str
    reachable: bool
    error: Optional[str] = None
    entries_analyzed: int = 0
    campaigns_found: int = 0
    source_id: str = ""
    label: str = ""
    category: str = ""
    official: bool = False
    http_status: Optional[int] = None
    content_type: str = ""
    robots_blocked: bool = False


def _normalize(text: str) -> str:
    return normalize(text)


def _extract_project(title: str, summary: str) -> str:
    """Best-effort project name extraction from title/summary.

    This is intentionally simple and does not invent names.
    """
    combined = f"{title} {summary}".strip()
    if not combined:
        return ""
    title_norm = _normalize(title)
    if title_norm and len(title_norm) <= 80 and not title_norm.lower().startswith(
        ("http", "www", "rt", "re:", "update")
    ):
        return title_norm
    summary_norm = _normalize(summary)
    if summary_norm:
        return summary_norm[:80].strip()
    return ""


def _extract_url(entry: Any, source: str) -> str:
    link = ""
    if isinstance(entry, dict):
        link = str(entry.get("link", "") or "").strip()
        content = entry.get("content")
        if isinstance(content, list) and content:
            c = content[0]
            if isinstance(c, dict):
                link = str(c.get("value", link) or "").strip()
    link = _normalize(link)
    if not link:
        return ""
    if not link.startswith(("http://", "https://")):
        if source.startswith("https://") and link.startswith("/"):
            return f"{source.rstrip('/')}/{link.lstrip('/')}"
        return ""
    return link


def _extract_published(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    for key in ("published", "updated", "pubDate", "date"):
        value = entry.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return None


def _extract_requirements_from_text(text: str) -> List[str]:
    """Quote requirement-like sentences from the entry text."""
    requirements: List[str] = []
    for sentence in re.split(r"[.;\n]", text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(
            signal in lowered
            for signal in (
                "requirement",
                "must ",
                "required",
                "eligibility",
                "eligible",
                "need to",
            )
        ) and len(sentence) <= 200:
            requirements.append(sentence[:160])
        if len(requirements) >= 5:
            break
    return requirements


def _classify_confidence(
    title: str, summary: str, config: Dict[str, Any]
) -> Tuple[bool, bool]:
    """Return (is_confirmed, is_speculation) based on source text only."""
    combined = f"{title} {summary}".lower()
    discovery_cfg = config.get("discovery", {})
    confirmation_signals = discovery_cfg.get("confirmation_signals", [])
    speculation_signals = discovery_cfg.get("speculation_signals", [])
    has_confirmation = any(str(s).lower() in combined for s in confirmation_signals)
    has_speculation = any(str(s).lower() in combined for s in speculation_signals)
    return has_confirmation, has_speculation


def _classify_risk(flags: List[str], config: Dict[str, Any]) -> str:
    """Risk tier from the detected flags. Low risk never means safe.

    Delegates to :func:`risk.risk_tier` so the tier logic has exactly one
    implementation.
    """
    return risk_tier(flags, config)


def validate_url(url: str) -> bool:
    """Validate a source URL is a plausible HTTPS feed URL."""
    url = (url or "").strip()
    if not url:
        return False
    if not url.startswith("https://"):
        return False
    if len(url) > 2048:
        return False
    if any(bad in url.lower() for bad in [" javascript:", " data:", " file:"]):
        return False
    return True


class FeedError(Exception):
    """A feed fetch or parse failure (kept for backward compatibility)."""

    def __init__(self, url: str, error_type: str, message: str) -> None:
        self.url = url
        self.error_type = error_type
        self.message = message
        super().__init__(f"Feed error ({url}): {error_type}")


def fetch_feed(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[Any]:
    """Fetch and parse a feed. Returns the parsed feed or raises FeedError."""
    if not validate_url(url):
        return None
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception as exc:
        raise FeedError(url, type(exc).__name__, str(exc)[:200]) from exc


def _matches_keywords(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(str(keyword).lower() in lowered for keyword in keywords)


def _build_campaign(
    item: RawItem,
    spec: SourceSpec,
    config: Dict[str, Any],
) -> Optional[Campaign]:
    """Turn one raw source entry into an analysed campaign, or None."""
    keywords = config.get("discovery", {}).get("campaign_keywords", []) or []
    title = _normalize(item.title)[:120]
    if not title:
        return None
    summary = _normalize(item.summary)
    gate_text = f"{title} {summary}"
    if keywords and not _matches_keywords(gate_text, keywords):
        return None

    summary_short = summary[:350]
    project = _extract_project(title, summary) or title
    url = item.url or item.listing_url
    official_url = item.official_url or ""

    is_confirmed, is_speculation = _classify_confidence(title, summary, config)

    rule_patterns: List[Tuple[str, List[str]]] = []
    for rule in config.get("risk_rules", []) or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id", ""))
        patterns = [str(p).lower() for p in rule.get("patterns", []) if str(p).strip()]
        if patterns:
            rule_patterns.append((rule_id, patterns))

    text_for_flags = gate_text.lower()
    flags: List[str] = []
    for rule_id, patterns in rule_patterns:
        if any(pattern in text_for_flags for pattern in patterns):
            flags.append(rule_id)

    extra_flags: Dict[str, str] = {}
    consistent, mismatch_note = domain_consistency(url, official_url)
    if not consistent:
        if "domain_mismatch" not in flags:
            flags.append("domain_mismatch")
        extra_flags["domain_mismatch"] = mismatch_note

    opportunity = classify_opportunity(title, summary, config)
    zero_capital = evaluate_zero_capital(title, summary, config)
    deadline = extract_deadline(summary)
    reward = extract_reward(summary, config)
    tasks = extract_tasks(summary)
    eligibility = extract_eligibility(summary)
    requirements = _extract_requirements_from_text(summary) or eligibility
    risk = score_risk(flags, config, extra_flags=extra_flags)

    campaign = Campaign(
        title=title[:100],
        project=project[:80],
        url=url,
        source=spec.url,
        published=item.published,
        requirements=requirements,
        risk_flags=flags,
        classification=_classify_risk(flags, config),
        is_confirmed=is_confirmed,
        is_speculation=is_speculation,
        raw_summary=summary_short,
        source_id=spec.id,
        source_label=spec.label,
        source_category=spec.category,
        source_is_official=spec.official,
        announcement_url=item.listing_url or url,
        official_url=official_url,
        opportunity_type=opportunity.type,
        opportunity_reason=opportunity.reason,
        deadline=deadline.iso if deadline else None,
        deadline_text=deadline.text if deadline else None,
        eligibility=eligibility,
        tasks=tasks,
        reward_status=reward.status,
        reward_text=reward.text,
        reward_note=reward.note,
        zero_capital_ok=zero_capital.ok,
        zero_capital_reason=zero_capital.reason,
        zero_capital_blockers=zero_capital.blockers,
        unknown_costs=zero_capital.unknown_costs,
        risk_score=risk.score,
        risk_band=risk.band,
        risk_explanation=risk.explanation,
        published_ts=item.published_ts,
    )
    campaign.campaign_id = campaign_id_for(campaign)
    return campaign


def campaign_id_for(campaign: Campaign) -> str:
    """Stable identifier for a campaign across runs.

    Derived from the project key, the title and the target URL, so the same
    campaign keeps the same id even when the report is rebuilt from a
    different run. Nothing time-dependent is included.
    """
    key = project_key(campaign.project, campaign.title)
    parts = [
        key,
        _normalize(campaign.title).lower(),
        _normalize(campaign.url).lower(),
    ]
    if not campaign.url:
        parts.append(_normalize(campaign.source).lower())
    joined = "\x00".join(part for part in parts if part)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def _priority_rank(campaign: Campaign) -> int:
    """Lower sorts first: zero-capital first, then unknown, then blockers."""
    if campaign.zero_capital_ok is True:
        return 0
    if campaign.zero_capital_ok is None:
        return 1
    return 2


def _sort_key(campaign: Campaign) -> Tuple[int, int, float]:
    recency = -(campaign.published_ts or 0.0)
    return (_priority_rank(campaign), campaign.risk_score, recency)


def _health_from_result(result: SourceResult, found: int) -> SourceHealth:
    spec = result.spec
    return SourceHealth(
        url=spec.url,
        reachable=result.reachable,
        error=result.error,
        entries_analyzed=result.entries_seen,
        campaigns_found=found,
        source_id=spec.id,
        label=spec.label,
        category=spec.category,
        official=spec.official,
        http_status=result.http_status,
        content_type=result.content_type,
        robots_blocked=result.robots_blocked,
    )


def scan_sources(
    specs: Sequence[SourceSpec],
    config: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
    min_interval: Optional[float] = None,
    respect_robots: Optional[bool] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[List[Campaign], List[SourceHealth], List[str]]:
    """Read every source and return campaigns, health and errors."""
    if config is None:
        config = load_config()
    policy = config.get("source_policy", {}) or {}
    user_agent = str(policy.get("user_agent") or USER_AGENT)
    timeout_value = int(
        timeout if timeout is not None else policy.get("request_timeout_seconds", REQUEST_TIMEOUT)
    )
    interval = float(
        min_interval
        if min_interval is not None
        else policy.get("min_interval_seconds", DEFAULT_MIN_INTERVAL)
    )
    use_robots = bool(
        respect_robots if respect_robots is not None else policy.get("respect_robots", True)
    )
    max_entries = int(policy.get("max_entries_per_source", MAX_ENTRIES_PER_FEED))
    max_findings = int(policy.get("max_findings", MAX_FINDINGS))

    campaigns: List[Campaign] = []
    health_list: List[SourceHealth] = []
    errors: List[str] = []

    for spec in list(specs)[:MAX_FEEDS]:
        result = fetch_source(
            spec,
            user_agent=user_agent,
            timeout=timeout_value,
            min_interval=interval,
            respect_robots=use_robots,
            session=session,
        )
        found = 0
        for item in result.items[:max_entries]:
            campaign = _build_campaign(item, spec, config)
            if campaign is None:
                continue
            campaigns.append(campaign)
            found += 1
        health_list.append(_health_from_result(result, found))
        if result.error:
            errors.append(f"Source error ({spec.id}): {result.error}")

    cross_check_campaigns(campaigns)

    campaigns.sort(key=_sort_key)
    return campaigns[:max_findings], health_list, errors


def scan_feeds(
    feed_urls: Sequence[str],
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Campaign], List[SourceHealth], List[str]]:
    """Backward-compatible wrapper: scan a plain list of feed URLs.

    Args:
        feed_urls: Feed URLs to scan. Honored exactly; the agent does not
            supplement these with other sources.
        config: Loaded vetted-sources configuration.

    Returns:
        A tuple of (campaigns, source_health, errors).
    """
    urls = [(url or "").strip() for url in feed_urls if (url or "").strip()]
    if not urls:
        return [], [], []
    specs = specs_from_urls(urls)
    valid_specs: List[SourceSpec] = []
    errors: List[str] = []
    for spec in specs:
        if not validate_source_url(spec.url):
            errors.append(f"Invalid feed URL skipped: {spec.url[:120]}")
            continue
        valid_specs.append(spec)
    if not valid_specs:
        return [], [], errors
    campaigns, health, scan_errors = scan_sources(valid_specs, config=config)
    return campaigns, health, errors + scan_errors


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _legacy_dedup_key(campaign: Campaign) -> str:
    """The V1.2 dedup key, kept so older state files still match."""
    parts = [campaign.title.strip().lower()]
    if campaign.source:
        parts.append(campaign.source.strip().lower())
    if campaign.url:
        parts.append(campaign.url.strip().lower())
    return "\x00".join(parts)


def _dedup_key(campaign: Campaign) -> Optional[str]:
    """Stable dedup key for a campaign (V1.3 campaign id)."""
    ident = campaign.campaign_id or campaign_id_for(campaign)
    if not ident:
        return None
    return ident


def deduplicate_campaigns(
    campaigns: List[Campaign],
    state: Dict[str, Any],
    max_entries: int = 200,
    stale_days: int = 7,
) -> Tuple[List[Campaign], Dict[str, Any], List[Campaign]]:
    """Deduplicate campaigns against persistent state.

    Returns:
        A tuple of (new_campaigns, updated_state, duplicates_skipped).
    """
    now = datetime.now(timezone.utc)
    seen: Dict[str, Dict[str, Any]] = state.get("seen", {}) or {}
    updated = dict(state)
    updated["seen"] = dict(seen)
    new_campaigns: List[Campaign] = []
    duplicates_skipped: List[Campaign] = []

    for campaign in campaigns:
        key = _dedup_key(campaign)
        if not key:
            new_campaigns.append(campaign)
            continue
        campaign.campaign_id = key
        previous = updated["seen"].get(key)
        legacy_key = _legacy_dedup_key(campaign)
        if previous is None and legacy_key in updated["seen"]:
            previous = updated["seen"][legacy_key]
            # Upgrade the record to the stable id while keeping its history.
            updated["seen"].pop(legacy_key, None)
        if previous:
            last_seen_raw = previous.get("last_seen") or previous.get("first_seen") or ""
            try:
                last_seen = datetime.fromisoformat(str(last_seen_raw))
            except ValueError:
                last_seen = now
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age_days = (now - last_seen).days
            if age_days < stale_days:
                first_seen = previous.get("first_seen") or last_seen_raw
                updated["seen"][key] = {
                    "last_seen": now.isoformat(),
                    "first_seen": first_seen,
                    "title": campaign.title,
                    "source": campaign.source,
                    "classification": campaign.classification,
                    "opportunity_type": campaign.opportunity_type,
                    "notified": bool(previous.get("notified", False)),
                }
                duplicates_skipped.append(campaign)
                continue
        updated["seen"][key] = {
            "last_seen": now.isoformat(),
            "first_seen": (previous or {}).get("first_seen", now.isoformat()),
            "title": campaign.title,
            "source": campaign.source,
            "classification": campaign.classification,
            "opportunity_type": campaign.opportunity_type,
            "notified": bool((previous or {}).get("notified", False)),
        }
        new_campaigns.append(campaign)

    if len(updated["seen"]) > max_entries:
        items = sorted(
            updated["seen"].items(),
            key=lambda item: str(item[1].get("last_seen", "")),
        )
        updated["seen"] = dict(items[-max_entries:])

    updated["updated_at"] = now.isoformat()
    return new_campaigns, updated, duplicates_skipped
