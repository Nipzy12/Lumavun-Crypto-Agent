"""Controlled registry onboarding. Review metadata is committed code, never remote input."""
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from source_security import validate_public_url

LEGACY_ENDPOINTS = {
    'bankless-academy-lessons':'https://app.banklessacademy.com/api/lessons',
    'airdropalert-rss':'https://airdropalert.com/feed/rssfeed',
    'airdrops-io-rss':'https://airdrops.io/feed/',
    'airdropalert-api':'https://airdropalert.com/wp-json/wp/v2/posts?per_page=10&_fields=id,date_gmt,link,title,excerpt',
    'decrypt-rss':'https://decrypt.co/feed',
    'ethereum-blog-rss':'https://blog.ethereum.org/en/feed.xml',
    'arbitrum-blog-rss':'https://blog.arbitrum.io/rss/',
}
ROOT = Path(__file__).resolve().parent.parent


def validate_onboarding(spec):
    if spec.id in LEGACY_ENDPOINTS:
        return None if spec.url == LEGACY_ENDPOINTS[spec.id] else 'Legacy source identity/endpoint mismatch'
    if spec.id.startswith('env:') and not spec.official:
        return None
    record = spec.onboarding
    if record.get('status') != 'APPROVED' or record.get('endpoint') != spec.url:
        return 'Source onboarding approval/endpoint binding missing'
    for key in ('ownership_url','documentation_url','permission_url'):
        if not validate_public_url(record.get(key)):
            return 'Missing valid provenance field: ' + key
    from campaign_sources import OPTIMISM_API, OPTIMISM_REPO
    source_host = urlsplit(spec.url).hostname
    owner_host = urlsplit(record['ownership_url']).hostname
    if source_host != owner_host:
        if not (spec.url == OPTIMISM_API and record['ownership_url'] == 'https://github.com/' + OPTIMISM_REPO):
            return 'Cross-host ownership binding is not approved'
    if not record.get('verified_at') or record.get('expected_parser') != spec.parser:
        return 'Source verification timestamp or parser binding missing'
    fixture = (ROOT / str(record.get('fixture',''))).resolve()
    if not fixture.is_relative_to(ROOT / 'tests' / 'fixtures') or not fixture.is_file():
        return 'Reviewed parser fixture is missing or outside fixture directory'
    if hashlib.sha256(fixture.read_bytes()).hexdigest() != record.get('fixture_sha256'):
        return 'Reviewed parser fixture changed; onboarding review required'
    return None
