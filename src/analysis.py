"""
Campaign analysis (V1.3): extraction, classification, zero-capital filter,
risk scoring and cross-checking.

Everything in this module is a pure function of text that a source actually
published. Nothing here invents a project, a deadline, a task or a reward:
when the payload does not say it, the result says "not stated".

The distinction that matters most:

* Facts are quoted from the source (``deadline_text``, ``reward_text``,
  ``tasks``).
* Interpretations are labelled as heuristics (``opportunity_type``,
  ``zero_capital_ok``, ``risk_score``). A POINTS_PROGRAM is never described
  as a guaranteed token reward, and a low risk score is never a promise of
  safety.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

DEADLINE_WORDS = (
    "deadline",
    "ends on",
    "ends ",
    "closes",
    "closing",
    "expires",
    "until",
    "before ",
    "by ",
    "final day",
    "last day",
    "cut-off",
    "cutoff",
)

ELIGIBILITY_SIGNALS = (
    "eligible",
    "eligibility",
    "must hold",
    "wallet must",
    "requires",
    "only for",
    "qualify",
    "qualifies",
    "criteria",
)

TASK_SIGNALS = (
    "follow",
    "join",
    "connect wallet",
    "connect your wallet",
    "submit",
    "verify",
    "complete",
    "check-in",
    "check in",
    "register",
    "sign up",
    "retweet",
    "react",
    "bridge",
    "swap",
    "mint",
    "stake",
    "deploy",
    "add liquidity",
    "provide liquidity",
    "hold ",
    "vote",
)

REWARD_SIGNALS = (
    "reward",
    "rewards",
    "allocation",
    "distribut",
    "airdrop",
    "tokens",
    "$",
    "supply",
    "points",
    "prize",
    "share of",
)

AMOUNT_RE = re.compile(
    r"(\d[\d,\.]*)\s*(billion|million|thousand|bn|m|k)?\s*(\$[A-Za-z]{2,10}|\$?[A-Z]{2,10})?\b"
)
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s?%")

YEAR_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
DAY_MONTH_YEAR_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|january|feb|february|mar|march|apr|april|may|"
    r"jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|"
    r"dec|december)\.?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)
MONTH_DAY_YEAR_RE = re.compile(
    r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|"
    r"sep|sept|september|oct|october|nov|november|dec|december)\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)


def normalize(text: Any) -> str:
    """Collapse whitespace and strip control characters."""
    if not text:
        return ""
    value = str(text).replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sentences(text: str) -> List[str]:
    """Split text into coarse sentences (also splits on line breaks)."""
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _clip(text: str, limit: int = 200) -> str:
    value = normalize(text)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "\u2026"


# ---------------------------------------------------------------------------
# Deadline, eligibility, tasks, reward
# ---------------------------------------------------------------------------


@dataclass
class Deadline:
    """A deadline quoted from the source."""

    iso: str
    text: str


def _parse_date_in(text: str) -> Optional[Tuple[int, int, int]]:
    match = YEAR_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return year, month, day
    match = DAY_MONTH_YEAR_RE.search(text)
    if match:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower())
        year = int(match.group(3))
        if month:
            return year, month, day
    match = MONTH_DAY_YEAR_RE.search(text)
    if match:
        month = MONTHS.get(match.group(1).lower())
        day = int(match.group(2))
        year = int(match.group(3))
        if month:
            return year, month, day
    return None


def extract_deadline(text: str, now: Optional[date] = None) -> Optional[Deadline]:
    """Find an explicitly dated deadline.

    Only an explicit calendar date in a sentence that talks about an ending
    is accepted. Relative phrases ("in two weeks") are not converted into a
    date, because that would be an invention.
    """
    today = now or date.today()
    for sentence in sentences(text):
        lowered = sentence.lower()
        if not any(word in lowered for word in DEADLINE_WORDS):
            continue
        parsed = _parse_date_in(sentence)
        if not parsed:
            continue
        year, month, day = parsed
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if value.year < today.year - 1 or value.year > today.year + 5:
            continue
        return Deadline(iso=value.isoformat(), text=_clip(sentence, 180))
    return None


def extract_eligibility(text: str, limit: int = 4) -> List[str]:
    """Quote short sentences that state eligibility criteria."""
    found: List[str] = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if any(signal in lowered for signal in ELIGIBILITY_SIGNALS):
            clipped = _clip(sentence, 200)
            if clipped and clipped not in found:
                found.append(clipped)
        if len(found) >= limit:
            break
    return found


def extract_tasks(text: str, limit: int = 5) -> List[str]:
    """Quote short sentences that describe an action to perform."""
    found: List[str] = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if any(signal in lowered for signal in TASK_SIGNALS):
            clipped = _clip(sentence, 180)
            if clipped and clipped not in found:
                found.append(clipped)
        if len(found) >= limit:
            break
    return found


@dataclass
class RewardInfo:
    """What the source says about the reward, and how strong that is."""

    status: str = "unspecified"  # stated | points_only | speculative | unspecified
    text: Optional[str] = None
    note: str = ""


def extract_reward(text: str, config: Dict[str, Any]) -> RewardInfo:
    """Extract the reward sentence and classify how certain it is.

    ``status`` values:

    * ``stated``      - the source states an amount/allocation.
    * ``points_only`` - the source only describes points or a leaderboard.
    * ``speculative`` - the source hedges ("may", "expected", "proposed").
    * ``unspecified`` - no reward statement at all.
    """
    opportunity = config.get("opportunity", {}) or {}
    statements = opportunity.get("reward_statements", {}) or {}
    speculative_phrases = [
        str(p).lower() for p in opportunity.get("speculative_reward_phrases", []) or []
    ]
    speculation_signals = [
        str(p).lower()
        for p in config.get("discovery", {}).get("speculation_signals", []) or []
    ]

    candidates: List[str] = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if any(signal in lowered for signal in REWARD_SIGNALS):
            candidates.append(sentence)

    if not candidates:
        return RewardInfo(status="unspecified", text=None, note="Sumber tidak menyebut hadiah.")

    # Prefer a sentence that actually carries a number or a token amount.
    best = candidates[0]
    for sentence in candidates:
        if PERCENT_RE.search(sentence) or _has_amount(sentence):
            best = sentence
            break

    lowered = best.lower()
    speculative = any(p in lowered for p in speculative_phrases) or any(
        p in lowered for p in speculation_signals
    )
    has_amount = bool(PERCENT_RE.search(best) or _has_amount(best))
    points_only = any(
        p in lowered for p in statements.get("points_only", []) or []
    ) and not has_amount
    stated = any(p in lowered for p in statements.get("stated", []) or [])

    if speculative:
        status = "speculative"
        note = "Sumber menyatakan hadiah secara spekulatif; bukan janji token."
    elif points_only:
        status = "points_only"
        note = "Hanya poin/leaderboard yang disebut; tidak ada janji token."
    elif stated and has_amount:
        status = "stated"
        note = "Jumlah hadiah disebut eksplisit oleh sumber."
    elif has_amount:
        status = "stated"
        note = "Sumber menyebut angka hadiah."
    else:
        status = "unspecified"
        note = "Sumber menyebut hadiah tanpa jumlah yang jelas."

    return RewardInfo(status=status, text=_clip(best, 220), note=note)


def _has_amount(text: str) -> bool:
    for match in AMOUNT_RE.finditer(text):
        digits = match.group(1) or ""
        if not digits:
            continue
        if match.group(2) or match.group(3):
            return True
        cleaned = digits.replace(",", "").replace(".", "")
        if len(cleaned) >= 3:
            return True
    return False


# ---------------------------------------------------------------------------
# Opportunity classification
# ---------------------------------------------------------------------------


OPPORTUNITY_TYPES = (
    "CONFIRMED_AIRDROP",
    "POINTS_PROGRAM",
    "TESTNET_POTENTIAL",
    "FREE_QUEST",
    "UNVERIFIED",
)


@dataclass
class Opportunity:
    """The opportunity type plus the words that produced it."""

    type: str = "UNVERIFIED"
    reason: str = ""
    matched_signals: List[str] = field(default_factory=list)


def _hits(text: str, signals: Sequence[str]) -> List[str]:
    lowered = text.lower()
    return [signal for signal in signals if str(signal).lower() in lowered]


def classify_opportunity(
    title: str,
    text: str,
    config: Dict[str, Any],
) -> Opportunity:
    """Classify an entry into one of :data:`OPPORTUNITY_TYPES`.

    Order of reasoning:

    1. Any speculation signal makes the entry ``UNVERIFIED``. The agent will
       not upgrade rumor to fact.
    2. An airdrop claim needs BOTH an airdrop word and a confirmation word
       (announced, snapshot, distribution, eligible, ...) to be called
       ``CONFIRMED_AIRDROP``.
    3. Otherwise points, testnet and quest signals are checked in order.
    """
    combined = f"{title} {text}"
    opportunity = config.get("opportunity", {}) or {}
    signals_cfg = opportunity.get("signals", {}) or {}
    discovery_cfg = config.get("discovery", {}) or {}
    speculation = _hits(combined, discovery_cfg.get("speculation_signals", []) or [])
    confirmation = _hits(combined, discovery_cfg.get("confirmation_signals", []) or [])
    downgrade = _hits(combined, opportunity.get("downgrade_phrases", []) or [])

    if speculation:
        return Opportunity(
            type="UNVERIFIED",
            reason="Teks sumber memuat sinyal spekulasi: " + ", ".join(speculation[:3]),
            matched_signals=speculation[:5],
        )

    airdrop_hits = _hits(combined, signals_cfg.get("CONFIRMED_AIRDROP", []) or [])
    points_hits = _hits(combined, signals_cfg.get("POINTS_PROGRAM", []) or [])
    testnet_hits = _hits(combined, signals_cfg.get("TESTNET_POTENTIAL", []) or [])
    quest_hits = _hits(combined, signals_cfg.get("FREE_QUEST", []) or [])

    if airdrop_hits and confirmation and not downgrade:
        return Opportunity(
            type="CONFIRMED_AIRDROP",
            reason=(
                "Menyebut airdrop ("
                + ", ".join(airdrop_hits[:2])
                + ") bersama bukti konfirmasi ("
                + ", ".join(confirmation[:2])
                + ")."
            ),
            matched_signals=(airdrop_hits + confirmation)[:6],
        )

    if points_hits:
        return Opportunity(
            type="POINTS_PROGRAM",
            reason="Menyebut program poin/leaderboard: " + ", ".join(points_hits[:3]),
            matched_signals=points_hits[:5],
        )

    if testnet_hits:
        return Opportunity(
            type="TESTNET_POTENTIAL",
            reason="Menyebut testnet/node/faucet: " + ", ".join(testnet_hits[:3]),
            matched_signals=testnet_hits[:5],
        )

    if quest_hits:
        return Opportunity(
            type="FREE_QUEST",
            reason="Menyebut quest/tugas: " + ", ".join(quest_hits[:3]),
            matched_signals=quest_hits[:5],
        )

    if airdrop_hits:
        return Opportunity(
            type="UNVERIFIED",
            reason="Menyebut airdrop tanpa bukti konfirmasi yang bisa dikutip.",
            matched_signals=airdrop_hits[:5],
        )

    return Opportunity(
        type="UNVERIFIED",
        reason="Tidak ada kata kunci kampanye yang bisa diklasifikasikan.",
        matched_signals=[],
    )


# ---------------------------------------------------------------------------
# Zero-capital filter
# ---------------------------------------------------------------------------


@dataclass
class ZeroCapital:
    """Whether an opportunity can be joined without spending money."""

    ok: Optional[bool] = None  # True / False / None == unknown
    reason: str = ""
    blockers: List[str] = field(default_factory=list)
    unknown_costs: List[str] = field(default_factory=list)
    onchain_actions: List[str] = field(default_factory=list)
    offchain_actions: List[str] = field(default_factory=list)


def _negated(sentence: str, match_start: int, negations: Sequence[str]) -> bool:
    window = sentence[max(0, match_start - 45) : match_start].lower()
    return any(neg in window for neg in negations)


def evaluate_zero_capital(
    title: str,
    text: str,
    config: Dict[str, Any],
) -> ZeroCapital:
    """Evaluate the zero-capital filter from the source text alone.

    A blocker that the source explicitly negates ("no deposit required") is
    not counted. On-chain actions are allowed but their gas cost is reported
    as unknown unless the source says gas is free.
    """
    zc = config.get("zero_capital", {}) or {}
    negations = [str(n).lower() for n in zc.get("negations", []) or []]
    combined = f"{title}. {text}"
    lowered = combined.lower()

    blockers: List[str] = []
    unknown: List[str] = []

    for pattern in zc.get("blockers", []) or []:
        needle = str(pattern).lower()
        if not needle:
            continue
        for match in re.finditer(re.escape(needle), lowered):
            if _negated(lowered, match.start(), negations):
                continue
            if needle not in blockers:
                blockers.append(needle)
            break

    onchain = [
        str(action).lower()
        for action in zc.get("onchain_actions", []) or []
        if str(action).lower() in lowered
    ]
    offchain = [
        str(action).lower()
        for action in zc.get("offchain_actions", []) or []
        if str(action).lower() in lowered
    ]
    free_gas = any(
        str(p).lower() in lowered for p in zc.get("free_gas_phrases", []) or []
    )

    if blockers:
        return ZeroCapital(
            ok=False,
            reason="Menyebut biaya atau setoran: " + ", ".join(blockers[:3]),
            blockers=blockers,
            unknown_costs=unknown,
            onchain_actions=onchain,
            offchain_actions=offchain,
        )

    if onchain and not free_gas:
        return ZeroCapital(
            ok=None,
            reason=(
                "Ada aksi on-chain ("
                + ", ".join(onchain[:3])
                + ") tetapi biaya gas tidak disebutkan."
            ),
            blockers=[],
            unknown_costs=["Biaya gas transaksi on-chain tidak disebutkan sumber"],
            onchain_actions=onchain,
            offchain_actions=offchain,
        )

    if onchain and free_gas:
        return ZeroCapital(
            ok=True,
            reason="Aksi on-chain dengan gas gratis/faucet yang disebut sumber.",
            onchain_actions=onchain,
            offchain_actions=offchain,
        )

    if offchain:
        return ZeroCapital(
            ok=True,
            reason="Hanya aksi off-chain: " + ", ".join(offchain[:3]),
            offchain_actions=offchain,
        )

    return ZeroCapital(
        ok=True,
        reason="Tidak ada setoran, pembelian, atau biaya yang disebutkan.",
    )


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


@dataclass
class RiskScore:
    score: int = 0
    band: str = "low"
    explanation: List[str] = field(default_factory=list)


def score_risk(
    flags: Sequence[str],
    config: Dict[str, Any],
    extra_flags: Optional[Dict[str, str]] = None,
) -> RiskScore:
    """Turn risk flags into a bounded numeric score with a readable trail.

    This is a heuristic indicator. A low score is not a safety guarantee.
    """
    scoring = config.get("risk_scoring", {}) or {}
    weights = {str(k): int(v) for k, v in (scoring.get("weights", {}) or {}).items()}
    bands = scoring.get("bands", {}) or {}
    cap = int(scoring.get("cap", 100) or 100)
    low_max = int(bands.get("low_max", 20) or 20)
    medium_max = int(bands.get("medium_max", 50) or 50)

    labels = {
        str(rule.get("id")): str(rule.get("label", rule.get("id")))
        for rule in config.get("risk_rules", []) or []
        if isinstance(rule, dict)
    }

    explanation: List[str] = []
    total = 0
    seen = set()
    for flag in flags:
        key = str(flag)
        if key in seen:
            continue
        seen.add(key)
        weight = weights.get(key, 0)
        if weight <= 0:
            continue
        total += weight
        note = ""
        if extra_flags and key in extra_flags:
            note = f" \u2014 {extra_flags[key]}"
        explanation.append(f"{labels.get(key, key)} (+{weight}){note}")

    score = min(total, cap)
    if score <= low_max:
        band = "low"
    elif score <= medium_max:
        band = "medium"
    else:
        band = "high"

    if not explanation:
        explanation.append("Tidak ada indikator risiko yang cocok di teks sumber.")

    return RiskScore(score=score, band=band, explanation=explanation)


# ---------------------------------------------------------------------------
# Cross-checking
# ---------------------------------------------------------------------------


_STOPWORDS = {
    "the",
    "a",
    "an",
    "airdrop",
    "airdrops",
    "free",
    "new",
    "how",
    "to",
    "guide",
    "full",
    "best",
    "what",
    "is",
    "for",
    "and",
}


def project_key(project: str, title: str) -> str:
    """A normalized key used to spot the same project across sources."""
    base = normalize(project or title).lower()
    base = re.sub(r"[^a-z0-9 ]+", " ", base)
    tokens = [t for t in base.split() if t and t not in _STOPWORDS]
    return " ".join(tokens[:3])


def cross_check_campaigns(campaigns: Sequence[Any]) -> None:
    """Mark campaigns that several independent sources report.

    ``cross_checked`` is True only when at least two distinct source hosts
    describe the same project key in the same run. A single listing is never
    called verified. This mutates the campaign objects in place.
    """
    from urllib.parse import urlparse

    groups: Dict[str, List[Any]] = {}
    for campaign in campaigns:
        key = project_key(getattr(campaign, "project", ""), getattr(campaign, "title", ""))
        if not key:
            continue
        groups.setdefault(key, []).append(campaign)

    for key, group in groups.items():
        hosts = []
        for campaign in group:
            host = (urlparse(getattr(campaign, "source", "")).hostname or "").lower()
            if host and host not in hosts:
                hosts.append(host)
        if len(hosts) < 2:
            for campaign in group:
                if not getattr(campaign, "cross_check_note", ""):
                    campaign.cross_check_note = (
                        "Hanya satu sumber pada scan ini; belum dikonfirmasi silang."
                    )
            continue
        official = any(bool(getattr(c, "source_is_official", False)) for c in group)
        note = "Disebut oleh {} sumber berbeda: {}{}".format(
            len(hosts),
            ", ".join(hosts[:4]),
            " (termasuk sumber resmi)" if official else "",
        )
        for campaign in group:
            campaign.cross_checked = True
            campaign.cross_check_note = note


def domain_consistency(url: str, official_url: str) -> Tuple[bool, str]:
    """Compare a claim URL against the project site the source names.

    A mismatch does not prove fraud, but it is a real signal worth showing:
    the claim page and the project home live on different registrable
    domains.
    """
    from sources import registrable_domain

    claim_domain = registrable_domain(url)
    official_domain = registrable_domain(official_url)
    if not claim_domain or not official_domain:
        return True, ""
    if claim_domain == official_domain:
        return True, ""
    return False, f"{claim_domain} tidak sama dengan situs {official_domain}"
