import sys
from pathlib import Path

# Make src importable for the test suite.
src_root = Path(__file__).resolve().parent.parent / "src"
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

import pytest
from discovery import scan_feeds, deduplicate_campaigns, validate_url, Campaign
from config_loader import load_config


def make_feed_entry(title, link=None, summary=None, published=None, content=None):
    entry = {"title": title}
    if link:
        entry["link"] = link
    if summary:
        entry["summary"] = summary
    if published:
        entry["published"] = published
    if content:
        entry["content"] = content
    return entry


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def sample_campaign():
    return Campaign(
        title="Example Airdrop",
        project="Example Project",
        url="https://example.com/claim",
        source="https://feeds.example.com/feed.xml",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        requirements=["Hold 100 tokens before snapshot"],
        risk_flags=[],
        classification="REVIEW_REQUIRED",
        is_confirmed=False,
        is_speculation=False,
        raw_summary="Some summary text",
    )
