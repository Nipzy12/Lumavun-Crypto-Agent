"""
Airdrop discovery engine.

Reads RSS/Atom feeds configured through AIRDROP_RSS_FEEDS (or the
vetted-sources configuration as a reference), validates source URLs,
extracts campaign information, and distinguishes confirmed information
from speculation. It never invents campaigns or rewards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import feedparser
import requests

from config_loader import load_config

MAX_FEEDS = 10
MAX_ENTRIES_PER_FEED = 15
MAX_FINDINGS = 20
REQUEST_TIMEOUT = 20


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
    classification: str = "REVIEW_REQUIRED"
    is_confirmed: bool = False
    is_speculation: bool = False
    raw_summary: str = ""


@dataclass
class SourceHealth:
    """Per-source health information for the report."""

    url: str
    reachable: bool
    error: Optional[str] = None
    entries_analyzed: int = 0
    campaigns_found: int = 0


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_project(title: str, summary: str) -> str:
    """Best-effort project name extraction from title/summary.

    This is intentionally simple and does not invent names.
    """
    combined = f"{title} {summary}".strip()
    if not combined:
        return ""
    # Prefer the title as the project name when it looks like a name.
    title_norm = _normalize(title)
    if title_norm and len(title_norm) <= 80 and not title_norm.lower().startswith(
        ("http", "www", "rt", "re:", "update")
    ):
        return title_norm
    # Otherwise fall back to a shortened summary lead.
    summary_norm = _normalize(summary)
    if summary_norm:
        lead = summary_norm[:80].strip()
        return lead
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
    # Keep only http/https links; drop feed-internal anchors without base.
    if not link.startswith(("http://", "https://")):
        # Try to resolve relative links against the source only if the source
        # is an https URL and the relative link looks like a path.
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


def _extract_requirements(entry: Any) -> List[str]:
    """Best-effort extraction of stated campaign requirements.

    Only returns text that appears in the feed entry. Never invents
    requirements.
    """
    requirements: List[str] = []
    if not isinstance(entry, dict):
        return requirements
    summary = str(entry.get("summary", "") or "").strip()
    title = str(entry.get("title", "") or "").strip()
    combined = f"{title} {summary}".lower()
    signals = [
        "requirement",
        "need",
        "must",
        "required",
        "eligibility",
        "eligible",
        "task",
        "quest",
        "hold",
        "snapshot",
        "window",
    ]
    # Pull short requirement-like phrases from the summary only when they
    # look like a real sentence fragment.
    if summary:
        sentences = re.split(r"[.;\n]", summary)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if any(signal in sentence.lower() for signal in signals) and len(
                sentence
            ) <= 200:
                requirements.append(sentence[:160])
    return requirements[:5]


def _classify_confidence(
    title: str, summary: str, config: Dict[str, Any]
) -> tuple[bool, bool]:
    """Return (is_confirmed, is_speculation) based on feed text.

    This is a heuristic that only uses text present in the feed entry. It
    never infers legitimacy beyond what the feed says.
    """
    combined = f"{title} {summary}".lower()
    confirmation_signals = config.get("discovery", {}).get(
        "confirmation_signals", []
    )
    speculation_signals = config.get("discovery", {}).get(
        "speculation_signals", []
    )
    has_confirmation = any(
        signal in combined for signal in confirmation_signals
    )
    has_speculation = any(
        signal in combined for signal in speculation_signals
    )
    return has_confirmation, has_speculation


def validate_url(url: str) -> bool:
    """Validate a source URL is a plausible HTTPS feed URL."""
    url = (url or "").strip()
    if not url:
        return False
    if not url.startswith("https://"):
        return False
    if len(url) > 2048:
        return False
    # Reject obvious non-feed patterns that are unlikely to be usable.
    if any(bad in url.lower() for bad in [" javascript:", " data:", " file:"]):
        return False
    return True


def fetch_feed(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[Any]:
    """Fetch and parse a feed. Returns parsed feed or None on failure."""
    if not validate_url(url):
        return None
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "LUMAVUN-Crypto-Agent/1.1"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if not parsed.get("entries"):
            # Some feeds are valid but have no entries; that is not an error.
            return parsed
        return parsed
    except Exception as exc:
        raise FeedError(url, type(exc).__name__, str(exc)[:200]) from exc


class FeedError(Exception):
    """A feed fetch or parse failure."""

    def __init__(self, url: str, error_type: str, message: str) -> None:
        self.url = url
        self.error_type = error_type
        self.message = message
        super().__init__(f"Feed error ({url}): {error_type}")


def scan_feeds(
    feed_urls: Sequence[str],
    config: Optional[Dict[str, Any]] = None,
) -> tuple[List[Campaign], List[SourceHealth], List[str]]:
    """Scan feeds and return campaigns, source health, and errors.

    Args:
        feed_urls: Feed URLs to scan. Honored exactly; the agent does not
            supplement these with other sources.
        config: Loaded vetted-sources configuration. Used only for keyword
            and rule references; never used to invent campaigns.

    Returns:
        A tuple of (campaigns, source_health, errors).
    """
    if config is None:
        config = {}
    risk_rules = config.get("risk_rules", [])
    rule_patterns: Dict[str, List[str]] = {}
    for rule in risk_rules:
        rule_id = str(rule.get("id", ""))
        patterns = [str(p).lower() for p in rule.get("patterns", []) if str(p).strip()]
        if patterns:
            rule_patterns[rule_id] = patterns

    campaigns: List[Campaign] = []
    health_list: List[SourceHealth] = []
    errors: List[str] = []

    feeds_to_scan = list(feed_urls)[:MAX_FEEDS]

    for url in feeds_to_scan:
        url = (url or "").strip()
        if not url:
            continue
        if not validate_url(url):
            errors.append(f"Invalid feed URL skipped: {url[:120]}")
            continue

        health = SourceHealth(url=url, reachable=False, entries_analyzed=0, campaigns_found=0)
        try:
            parsed = fetch_feed(url)
            if parsed is None:
                health.reachable = False
                health.error = "Invalid or skipped URL"
                health_list.append(health)
                continue
            entries = parsed.get("entries", [])
            health.reachable = True
            health.entries_analyzed = min(len(entries), MAX_ENTRIES_PER_FEED)
            found_for_source = 0
            for entry in entries[:MAX_ENTRIES_PER_FEED]:
                title = _normalize(str(entry.get("title", "") or "")).strip()
                if not title:
                    continue
                summary_raw = entry.get("summary", "")
                summary = _normalize(str(summary_raw or ""))
                summary_short = summary[:350]
                link = _extract_url(entry, url)
                published = _extract_published(entry)
                project = _extract_project(title, summary)
                requirements = _extract_requirements(entry)
                is_confirmed, is_speculation = _classify_confidence(
                    title, summary, config
                )
                text_for_flags = f"{title} {summary_short}".lower()
                flags: List[str] = []
                for rule_id, patterns in rule_patterns.items():
                    if any(pattern in text_for_flags for pattern in patterns):
                        flags.append(rule_id)
                classification = _classify_risk(flags, config)
                campaign = Campaign(
                    title=title[:100],
                    project=project[:80],
                    url=link,
                    source=url,
                    published=published,
                    requirements=requirements,
                    risk_flags=flags,
                    classification=classification,
                    is_confirmed=is_confirmed,
                    is_speculation=is_speculation,
                    raw_summary=summary_short,
                )
                campaigns.append(campaign)
                found_for_source += 1
            health.campaigns_found = found_for_source
        except FeedError as exc:
            health.reachable = False
            health.error = f"{exc.error_type}: {exc.message}"
            errors.append(f"Feed error ({exc.url}): {exc.error_type}")
        except Exception as exc:
            health.reachable = False
            health.error = f"Unexpected: {type(exc).__name__}"
            errors.append(f"Feed error ({url}): {type(exc).__name__}")
        health_list.append(health)

    return campaigns[:MAX_FINDINGS], health_list, errors


def _classify_risk(flags: List[str], config: Dict[str, Any]) -> str:
    """Classify a campaign based on detected risk flags.

    A low-risk classification never implies guaranteed safety.
    """
    high_ids = set(
        str(i)
        for i in config.get("classification", {}).get(
            "high_risk_indicator_ids", []
        )
    )
    moderate_ids = set(
        str(i)
        for i in config.get("classification", {}).get(
            "moderate_indicator_ids", []
        )
    )

    high_hits = [f for f in flags if f in high_ids]
    moderate_hits = [f for f in flags if f in moderate_ids]

    if high_hits:
        return "HIGH_RISK_INDICATORS"
    if moderate_hits:
        return "REVIEW_REQUIRED"
    return "LOW_RISK_INDICATORS"


def deduplicate_campaigns(
    campaigns: List[Campaign],
    state: Dict[str, Any],
    max_entries: int = 200,
    stale_days: int = 7,
) -> tuple[List[Campaign], Dict[str, Any], List[Campaign]]:
    """Deduplicate campaigns against persistent state.

    Returns:
        A tuple of (new_campaigns, updated_state, duplicates_skipped).
    """
    now = datetime.now(timezone.utc)
    seen: Dict[str, Dict[str, Any]] = state.get("seen", {})
    updated = dict(state)
    updated["seen"] = dict(seen)
    new_campaigns: List[Campaign] = []
    duplicates_skipped: List[Campaign] = []

    for campaign in campaigns:
        key = _dedup_key(campaign)
        if not key:
            # Unidentifiable campaigns are still reported as new.
            new_campaigns.append(campaign)
            continue
        previous = updated["seen"].get(key)
        if previous:
            last_seen = datetime.fromisoformat(previous["last_seen"])
            age_days = (now - last_seen).days
            if age_days < stale_days:
                duplicates_skipped.append(campaign)
                continue
        updated["seen"][key] = {
            "last_seen": now.isoformat(),
            "title": campaign.title,
            "source": campaign.source,
            "classification": campaign.classification,
        }
        new_campaigns.append(campaign)

    # Trim state to a bounded size.
    if len(updated["seen"]) > max_entries:
        items = sorted(
            updated["seen"].items(),
            key=lambda item: item[1]["last_seen"],
        )
        keep = dict(items[-max_entries:])
        updated["seen"] = keep

    updated["updated_at"] = now.isoformat()
    return new_campaigns, updated, duplicates_skipped


def _dedup_key(campaign: Campaign) -> Optional[str]:
    """Generate a stable dedup key for a campaign.

    Uses title + source + url when available. This is intentionally
    conservative: it avoids false deduplication but may let some
    repeats through.
    """
    parts = [campaign.title.strip().lower()]
    if campaign.source:
        parts.append(campaign.source.strip().lower())
    if campaign.url:
        parts.append(campaign.url.strip().lower())
    key = "\x00".join(parts)
    if len(key) < 10:
        return None
    return key
