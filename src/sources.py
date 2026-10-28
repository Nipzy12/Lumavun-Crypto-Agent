"""
Source layer (V1.3).

Every place the agent reads from is described by a :class:`SourceSpec`, so
adding a source is a configuration change rather than a code change. Two
transports are supported:

* ``rss``  - RSS/Atom feeds parsed with feedparser.
* ``json`` - documented public JSON APIs, mapped by a named parser.

The layer is deliberately conservative about the network:

* ``robots.txt`` is fetched and honored per host (cached per origin).
* Requests are rate-limited per host.
* A single identifying User-Agent is sent.
* Responses that are not 2xx, not parseable, or too large become a
  per-source error instead of an exception, so one dead source cannot stop
  the run.
* Referral and tracking query parameters are stripped from every URL, so
  the agent never propagates someone else's referral code.

Nothing here invents data: an item exists only because the provider
published it in the payload that was actually received.
"""

from __future__ import annotations

import calendar
import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "LUMAVUN-Crypto-Agent/1.3 "
    "(+https://github.com/Nipzy12/Lumavun-Crypto-Agent)"
)
DEFAULT_TIMEOUT = 20
DEFAULT_MIN_INTERVAL = 1.5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SUMMARY_CHARS = 6000

# Query parameters that only exist to track or monetize a click. Dropping
# them keeps reported links clean and prevents referral propagation.
TRACKING_PARAMS = {
    "ref",
    "ref_",
    "ref_src",
    "referral",
    "referral_code",
    "referrer",
    "aff",
    "aff_id",
    "affiliate",
    "affiliate_id",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "msclkid",
    "clickid",
    "source",
    "src",
}

_LAST_REQUEST_AT: Dict[str, float] = {}
_ROBOTS_CACHE: Dict[str, Optional["RobotsRules"]] = {}
_ROBOTS_FETCHED: set[str] = set()


class SourceError(Exception):
    """A recoverable per-source failure (network, HTTP, parse, robots)."""

    def __init__(self, source_id: str, message: str, http_status: Optional[int] = None) -> None:
        self.source_id = source_id
        self.message = message
        self.http_status = http_status
        super().__init__(f"{source_id}: {message}")


@dataclass
class SourceSpec:
    """One readable source."""

    id: str
    url: str
    kind: str = "rss"  # "rss" | "json"
    parser: str = "feed"
    label: str = ""
    category: str = "airdrop_listing"
    official: bool = False
    enabled: bool = True
    notes: str = ""


@dataclass
class RawItem:
    """A single entry as published by a source, before any interpretation."""

    title: str
    summary: str
    url: str
    listing_url: str
    source_url: str
    source_id: str
    published: Optional[str] = None
    published_ts: Optional[float] = None
    official_url: str = ""
    extra_urls: List[str] = field(default_factory=list)
    entry_id: str = ""


@dataclass
class SourceResult:
    """Outcome of reading one source."""

    spec: SourceSpec
    items: List[RawItem] = field(default_factory=list)
    http_status: Optional[int] = None
    content_type: str = ""
    entries_seen: int = 0
    error: Optional[str] = None
    robots_blocked: bool = False

    @property
    def reachable(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------


def sanitize_url(url: str) -> str:
    """Return a clean http(s) URL with tracking parameters removed.

    Non-http(s) URLs (``javascript:``, ``data:``, ``mailto:``) and anything
    that does not parse become an empty string rather than being reported.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https"):
        return ""
    if not parsed.netloc:
        return ""
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    cleaned = parsed._replace(query=urlencode(pairs), fragment="")
    return urlunparse(cleaned)


def registrable_domain(url: str) -> str:
    """Best-effort registrable domain (``proof.soei.io`` -> ``soei.io``).

    This is a heuristic used for explainable risk signals, not a security
    boundary. It understands common two-label public suffixes.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    two_label_suffixes = {
        "co.uk",
        "org.uk",
        "ac.uk",
        "com.au",
        "co.jp",
        "com.br",
        "co.in",
        "com.sg",
    }
    if ".".join(parts[-2:]) in two_label_suffixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


@dataclass
class RobotsRules:
    """Minimal robots.txt rule set for the groups that apply to us."""

    allow: List[str] = field(default_factory=list)
    disallow: List[str] = field(default_factory=list)

    def allows(self, path: str) -> bool:
        target = path or "/"
        best_len = -1
        best_allow = True
        for prefix in self.disallow:
            if prefix and target.startswith(prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best_allow = False
        for prefix in self.allow:
            if prefix and target.startswith(prefix) and len(prefix) >= best_len:
                best_len = len(prefix)
                best_allow = True
        return best_allow


def parse_robots(text: str, user_agent: str) -> RobotsRules:
    """Parse robots.txt, keeping the groups that apply to ``user_agent``.

    Only a pragmatic subset is supported: ``User-agent`` groups (with ``*``
    and a token match), plus ``Allow``/``Disallow`` prefixes. Comments,
    ``Sitemap`` and unknown directives are ignored.
    """
    token = re.split(r"[/\s]", (user_agent or "").strip().lower())[0]
    wildcard = RobotsRules()
    specific = RobotsRules()
    current_targets: List[str] = []
    in_rules = False

    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if in_rules:
                current_targets = []
                in_rules = False
            current_targets.append(value.lower())
            continue
        if key not in ("allow", "disallow"):
            continue
        in_rules = True
        if not current_targets:
            continue
        if "*" in current_targets:
            bucket = wildcard
        elif any(token == target or token in target for target in current_targets):
            bucket = specific
        else:
            continue
        if not value:
            continue
        if key == "allow":
            bucket.allow.append(value)
        else:
            bucket.disallow.append(value)

    if specific.allow or specific.disallow:
        return specific
    return wildcard


def fetch_robots(origin: str, user_agent: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[RobotsRules]:
    """Fetch and cache robots.txt for an origin. ``None`` means no policy."""
    if origin in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[origin]
    _ROBOTS_FETCHED.add(origin)
    try:
        resp = requests.get(
            origin.rstrip("/") + "/robots.txt",
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
        if resp.status_code != 200:
            _ROBOTS_CACHE[origin] = None
            return None
        rules = parse_robots(resp.text, user_agent)
        _ROBOTS_CACHE[origin] = rules
        return rules
    except Exception:
        # Standard practice: an unreachable robots.txt is not a block.
        _ROBOTS_CACHE[origin] = None
        return None


def robots_allows(url: str, user_agent: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """True when robots.txt permits fetching ``url``."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rules = fetch_robots(origin, user_agent, timeout=timeout)
    if rules is None:
        return True
    return rules.allows(parsed.path or "/")


def reset_network_caches() -> None:
    """Clear robots and rate-limit caches (used by tests)."""
    _ROBOTS_CACHE.clear()
    _ROBOTS_FETCHED.clear()
    _LAST_REQUEST_AT.clear()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _respect_rate_limit(host: str, min_interval: float) -> None:
    if not host or min_interval <= 0:
        return
    last = _LAST_REQUEST_AT.get(host)
    now = time.monotonic()
    if last is not None:
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _LAST_REQUEST_AT[host] = time.monotonic()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def specs_from_config(config: Dict[str, Any]) -> List[SourceSpec]:
    """Build the enabled source list from the vetted-sources config."""
    specs: List[SourceSpec] = []
    for entry in config.get("sources", []) or []:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", "")).strip()
        if not url:
            continue
        kind = str(entry.get("kind", "rss")).lower()
        spec = SourceSpec(
            id=str(entry.get("id") or url),
            url=url,
            kind=kind,
            parser=str(
                entry.get("parser") or ("feed" if kind == "rss" else "wp_rest_posts")
            ),
            label=str(entry.get("label", "")),
            category=str(entry.get("category", "airdrop_listing")),
            official=bool(entry.get("official", False)),
            enabled=bool(entry.get("enabled", True)),
            notes=str(entry.get("notes", "")),
        )
        if spec.enabled:
            specs.append(spec)
    return specs


def specs_from_urls(urls: List[str], category: str = "custom") -> List[SourceSpec]:
    """Wrap a plain list of feed URLs (the AIRDROP_RSS_FEEDS override)."""
    specs: List[SourceSpec] = []
    for url in urls:
        url = (url or "").strip()
        if not url:
            continue
        specs.append(
            SourceSpec(
                id=f"env:{url}",
                url=url,
                kind="rss",
                parser="feed",
                label="AIRDROP_RSS_FEEDS override",
                category=category,
                official=False,
                enabled=True,
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Turn an HTML fragment into collapsed plain text."""
    if not text:
        return ""
    value = _HTML_TAG_RE.sub(" ", str(text))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _entry_timestamp(entry: Any) -> Optional[float]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key) if hasattr(entry, "get") else None
        if value:
            try:
                return float(calendar.timegm(value))
            except Exception:
                continue
    return None


def _feed_summary(entry: Any) -> str:
    content = entry.get("content") if hasattr(entry, "get") else None
    if isinstance(content, list) and content:
        value = content[0].get("value") if isinstance(content[0], dict) else None
        if value:
            return strip_html(value)[:MAX_SUMMARY_CHARS]
    summary = entry.get("summary") if hasattr(entry, "get") else None
    if summary:
        return strip_html(summary)[:MAX_SUMMARY_CHARS]
    return ""


def parse_feed(payload: bytes, spec: SourceSpec) -> List[RawItem]:
    """Map an RSS/Atom payload onto :class:`RawItem` objects."""
    parsed = feedparser.parse(payload)
    entries = parsed.get("entries", []) or []
    items: List[RawItem] = []
    for entry in entries:
        title = strip_html(str(entry.get("title", "") or ""))
        if not title:
            continue
        listing_url = sanitize_url(str(entry.get("link", "") or ""))
        airdrop_link = sanitize_url(str(entry.get("airdrop_link", "") or ""))
        website_link = sanitize_url(str(entry.get("website_link", "") or ""))
        whitepaper = sanitize_url(str(entry.get("white_paper_link", "") or ""))
        extra: List[str] = [u for u in (airdrop_link, website_link, whitepaper) if u]
        published = None
        for key in ("published", "updated", "created"):
            value = entry.get(key)
            if value:
                published = str(value).strip()
                break
        items.append(
            RawItem(
                title=title[:300],
                summary=_feed_summary(entry),
                url=airdrop_link or listing_url,
                listing_url=listing_url or airdrop_link,
                source_url=spec.url,
                source_id=spec.id,
                published=published,
                published_ts=_entry_timestamp(entry),
                official_url=website_link,
                extra_urls=extra,
                entry_id=str(entry.get("id") or entry.get("post-id") or listing_url),
            )
        )
    return items


def parse_wp_rest_posts(payload: bytes, spec: SourceSpec) -> List[RawItem]:
    """Map a WordPress REST ``/wp/v2/posts`` JSON array onto raw items.

    Only documented fields are read: ``id``, ``date_gmt``, ``link``,
    ``title.rendered`` and ``excerpt.rendered``.
    """
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise SourceError(spec.id, "JSON response was not valid JSON") from exc
    if not isinstance(data, list):
        raise SourceError(spec.id, "JSON response was not a list of posts")
    items: List[RawItem] = []
    for post in data:
        if not isinstance(post, dict):
            continue
        title_block = post.get("title")
        if isinstance(title_block, dict):
            title = strip_html(str(title_block.get("rendered", "")))
        else:
            title = strip_html(str(title_block or ""))
        if not title:
            continue
        excerpt_block = post.get("excerpt")
        if isinstance(excerpt_block, dict):
            summary = strip_html(str(excerpt_block.get("rendered", "")))
        else:
            summary = strip_html(str(excerpt_block or ""))
        link = sanitize_url(str(post.get("link", "") or ""))
        published = str(post.get("date_gmt", "") or "") or None
        items.append(
            RawItem(
                title=title[:300],
                summary=summary[:MAX_SUMMARY_CHARS],
                url=link,
                listing_url=link,
                source_url=spec.url,
                source_id=spec.id,
                published=published,
                published_ts=_iso_to_ts(published),
                entry_id=f"wp-{post.get('id', '')}",
            )
        )
    return items


def _iso_to_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


PARSERS: Dict[str, Callable[[bytes, SourceSpec], List[RawItem]]] = {
    "feed": parse_feed,
    "wp_rest_posts": parse_wp_rest_posts,
}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def validate_source_url(url: str) -> bool:
    """A source URL must be HTTPS, sane, and not a local/private target."""
    raw = (url or "").strip()
    if not raw or not raw.startswith("https://"):
        return False
    if len(raw) > 2048:
        return False
    host = (urlparse(raw).hostname or "").lower()
    if not host or "." not in host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return False
    lowered = raw.lower()
    if any(bad in lowered for bad in ("javascript:", "data:", "file:")):
        return False
    return True


def fetch_source(
    spec: SourceSpec,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    respect_robots: bool = True,
    session: Optional[requests.Session] = None,
) -> SourceResult:
    """Read one source, converting every failure into a result error."""
    result = SourceResult(spec=spec)

    if not validate_source_url(spec.url):
        result.error = "Invalid or non-HTTPS source URL"
        return result

    parser = PARSERS.get(spec.parser) or parse_feed

    if respect_robots and not robots_allows(spec.url, user_agent, timeout=timeout):
        result.robots_blocked = True
        result.error = "robots.txt disallows this path"
        return result

    host = (urlparse(spec.url).hostname or "").lower()
    _respect_rate_limit(host, min_interval)

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(
            spec.url,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
        )
    except requests.RequestException as exc:
        result.error = f"Network error: {type(exc).__name__}"
        return result
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"Unexpected error: {type(exc).__name__}"
        return result

    result.http_status = resp.status_code
    result.content_type = (resp.headers.get("content-type") or "")[:80]

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "")
        result.error = "Rate limited (HTTP 429)" + (
            f", retry-after {retry_after}s" if retry_after else ""
        )
        return result
    if resp.status_code != 200:
        result.error = f"HTTP {resp.status_code}"
        return result

    payload = resp.content or b""
    if len(payload) > MAX_RESPONSE_BYTES:
        result.error = f"Response too large ({len(payload)} bytes)"
        return result
    if not payload:
        result.error = "Empty response body"
        return result

    try:
        items = parser(payload, spec)
    except SourceError as exc:
        result.error = exc.message
        return result
    except Exception as exc:
        result.error = f"Parse error: {type(exc).__name__}"
        return result

    result.items = items
    result.entries_seen = len(items)
    return result
