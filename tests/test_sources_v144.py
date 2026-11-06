"""Synthetic source fixtures; scores and program allocations never prove payouts."""
from copy import deepcopy
from pathlib import Path
import json
import socket
import pytest
from campaign_sources import OPTIMISM_API, parse_optimism_missions, explicit_date
from sources import SourceSpec, SourceResult, fetch_source, validate_source_url, parse_robots
from source_security import resolve_public as real_resolve_public
from config_loader import effective_sources, load_config
from discovery import _build_campaign, SourceHealth
from quests import update_queue
from qualification import qualify
from source_onboarding import validate_onboarding
from source_coverage import coverage_report
from test_quests_v14 import NOW, quest

FIXTURE = Path(__file__).parent/'fixtures/optimism_missions.json'


def spec():
    return next(s for s in effective_sources() if s.id=='optimism-foundation-missions')


def build(row=None):
    rows=json.loads(FIXTURE.read_text()) if row is None else [row]
    items=parse_optimism_missions(json.dumps(rows).encode(), spec())
    if not items:return None
    q=_build_campaign(items[0],spec(),load_config()).quest_intelligence
    update_queue({},[q],NOW)
    return q


def test_reviewed_fixture_extracts_explicit_campaign_fields():
    q=build()
    assert q['evidence_verified'] and q['project']=='Optimism Collective'
    assert q['source_verification_status']=='official_campaign_api'
    assert q['start_at']=='2026-11-01' and q['end_at']=='2026-12-01'
    assert q['reward_type']=='TOKEN' and q['reward_guaranteed'] is False
    assert q['cost_status']=='PASS' and q['gas_status']=='PASS'
    assert len(q['tasks'])==2
    assert q['qualification']['status']=='ACTIONABLE'
    assert q['qualification']['reward_class']=='CONDITIONAL_TOKEN_REWARD'
    assert q['provenance']['author_association']=='CONTRIBUTOR'  # official curated label, not arbitrary author identity


@pytest.mark.parametrize('old,new,field,expected',[
    ('100 OP','Unspecified allocation','evidence_verified',False),
    ('Participation is free.','Deposit required.','cost_status','FAIL'),
    ('Participation is free. Gas is sponsored.','Mint a token; gas fees may apply.','cost_status','UNKNOWN'),
    ('Open to everyone.','US residents only. KYC required.','eligibility_status','UNKNOWN'),
    ('December 1, 2026','December 1, 2020','status','EXPIRED'),
])
def test_incomplete_or_restricted_campaigns_do_not_pass(old,new,field,expected):
    row=json.loads(FIXTURE.read_text())[0];row['body']=row['body'].replace(old,new)
    q=build(row)
    assert q[field]==expected
    assert q['qualification']['status']!='ACTIONABLE'


@pytest.mark.parametrize('changes',[
    {'pull_request':{}}, {'state':'closed'}, {'labels':[]},
    {'repository_url':'https://api.github.com/repos/attacker/project'},
    {'html_url':'https://github.com/attacker/project/issues/900001'},
    {'html_url':'https://github.com.evil.test/ethereum-optimism/ecosystem-contributions/issues/900001'},
    {'number':123},
])
def test_unbound_or_uncurated_issues_are_excluded(changes):
    row=json.loads(FIXTURE.read_text())[0];row.update(changes)
    assert build(row) is None


def test_api_error_schema_is_not_a_campaign():
    from sources import SourceError
    with pytest.raises(SourceError):parse_optimism_missions(b'{"message":"rate limit exceeded"}',spec())


def test_truncated_terms_never_get_zero_cost_pass():
    row=json.loads(FIXTURE.read_text())[0];row['body']+='\n'+('padding '*4000)+'Deposit required.'
    q=build(row)
    assert q['cost_status']=='UNKNOWN' and q['qualification']['status']!='ACTIONABLE'


@pytest.mark.parametrize('date,expected', [('21 Jan 2025','2025-01-21'),('24 Jul 2025 (start of S8)\n\n---','2025-07-24'),('April 16th',None),('End of Season 9',None),('Applications accepted on a rolling basis',None)])
def test_dates_require_explicit_year(date,expected):
    assert explicit_date(date)==expected


@pytest.mark.parametrize('url',[
    'http://example.com/feed','https://127.0.0.1/feed','https://10.0.0.1/feed',
    'https://169.254.169.254/latest/meta-data/','https://[::1]/feed','https://[::ffff:127.0.0.1]/feed',
    'https://2130706433/feed','https://0x7f000001/feed','https://0177.0.0.1/feed',
    'https://user:pass@example.com/feed','https://example.com:8080/feed','https://example.local/feed',
    'https://example.com\\@127.0.0.1/feed','https://example.com/feed\n','https://metadata.google.internal/feed',
])
def test_malicious_source_url_rejected_before_requests(url,requests_mock):
    assert not validate_source_url(url)
    assert fetch_source(SourceSpec(id='bad',url=url)).error
    assert not requests_mock.request_history


@pytest.mark.parametrize('ips',[['127.0.0.1'],['10.0.0.1'],['93.184.216.34','169.254.169.254'],['::1']])
def test_all_dns_answers_must_be_public(ips,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in ips])
    with pytest.raises(ValueError):real_resolve_public('https://example.com/feed')


def test_dns_is_rechecked_before_requests(monkeypatch,requests_mock):
    calls=[]
    def changed(url):
        calls.append(url)
        if len(calls)>1:raise ValueError('Now points to a private address')
        return {'93.184.216.34'}
    monkeypatch.setattr('source_security.resolve_public',changed)
    outcome=fetch_source(SourceSpec(id='x',url='https://example.test/feed'))
    assert outcome.error and not requests_mock.request_history


def test_redirect_to_metadata_not_followed(requests_mock):
    requests_mock.get('https://example.test/robots.txt',status_code=404)
    requests_mock.get('https://example.test/feed',status_code=302,headers={'Location':'https://169.254.169.254/'})
    result=fetch_source(SourceSpec(id='x',url='https://example.test/feed'),min_interval=0)
    assert result.error=='HTTP 302'
    assert len(requests_mock.request_history)==2


def test_robots_wildcards_and_end_anchor_are_respected():
    rules=parse_robots('User-agent: *\nDisallow: /c/*.rss\nDisallow: /studio$','LUMAVUN')
    assert not rules.allows('/c/grants.rss') and not rules.allows('/studio')
    assert rules.allows('/studio-not-private')


def test_controlled_onboarding_requires_provenance_and_fixture():
    approved=spec();assert validate_onboarding(approved) is None
    for key,value in [('status','PENDING'),('endpoint','https://evil.test/feed'),('documentation_url','file:///tmp/doc'),('fixture','../../etc/passwd'),('fixture_sha256','changed')]:
        changed=deepcopy(approved);changed.onboarding[key]=value
        assert validate_onboarding(changed)


def test_unapproved_new_source_never_requested(requests_mock):
    proposed=SourceSpec(id='new',url='https://example.test/feed',require_onboarding=True)
    assert 'onboarding' in fetch_source(proposed).error
    assert not requests_mock.request_history


def test_source_failure_and_rate_limit_are_isolated(requests_mock):
    requests_mock.get('https://api.github.com/robots.txt',status_code=404)
    requests_mock.get(OPTIMISM_API,status_code=429,headers={'Retry-After':'120'})
    result=fetch_source(spec(),min_interval=0)
    assert not result.reachable and 'retry-after 120s' in result.error


def test_duplicate_announcements_keep_one_canonical_campaign():
    q=build();state={};update_queue(state,[q,deepcopy(q)],NOW)
    assert len(state['quest_queue'])==1
    health=SourceHealth(url=OPTIMISM_API,reachable=True,source_id=spec().id,observations=[q,deepcopy(q)])
    report=coverage_report([spec()],[health],state['quest_queue'],{q['id']},{},NOW)
    assert report['noneducational_candidates']==1 and report['newly_discovered_campaigns']==0


def test_education_and_points_are_not_monetary_yield():
    q=build();edu=deepcopy(q);edu['id']='education';edu['reward_type']='EDUCATIONAL_BADGE'
    edspec=SourceSpec(id='education',url='https://education.test/api',official=True,source_class='EDUCATION')
    hs=[SourceHealth(url=OPTIMISM_API,reachable=True,source_id=spec().id,observations=[q]),SourceHealth(url=edspec.url,reachable=True,source_id=edspec.id,observations=[edu])]
    report=coverage_report([spec(),edspec],hs,{q['id']:q,edu['id']:edu},set(),{},NOW)
    assert report['official_campaign_sources_available']==1
    assert report['educational_only_entries']==1 and report['verified_candidates']==1
    assert report['confirmed_received_rewards']==0
    assert report['source_quality'][1]['quality_score'] is None
    assert 0<=report['source_quality'][0]['quality_score']<=100


def test_quality_score_never_overrides_unknown_cost_gate():
    q=build();q['cost_status']='UNKNOWN'
    h=SourceHealth(url=OPTIMISM_API,reachable=True,source_id=spec().id,observations=[q])
    report=coverage_report([spec()],[h],{q['id']:q},set(),{},NOW)
    assert report['source_quality'][0]['quality_score']>70
    assert report['actionable_candidates']==0


def test_legacy_id_cannot_impersonate_an_unreviewed_endpoint():
    proposed=SourceSpec(id='ethereum-blog-rss',url='https://evil.test/feed',official=True,require_onboarding=True)
    assert 'mismatch' in validate_onboarding(proposed)


def test_source_ownership_cannot_point_to_an_unrelated_project():
    proposed=deepcopy(spec());proposed.onboarding['ownership_url']='https://evil.test/project'
    assert 'ownership' in validate_onboarding(proposed)


def test_streamed_response_size_is_bounded(requests_mock,monkeypatch):
    monkeypatch.setattr('sources.MAX_RESPONSE_BYTES',20)
    requests_mock.get('https://example.test/robots.txt',status_code=404)
    requests_mock.get('https://example.test/feed',content=b'x'*100)
    assert 'too large' in fetch_source(SourceSpec(id='x',url='https://example.test/feed'),min_interval=0).error


def test_attestation_requirement_is_not_inferred_payment_or_free_participation():
    row=json.loads(FIXTURE.read_text())[0]
    row['body']=row['body'].replace('Participation is free. Gas is sponsored. Open to everyone.', 'Candidates must hold one of the following attestations: Governance credentials.')
    q=build(row)
    assert q['cost_status']=='UNKNOWN' and q['eligibility_status']=='UNKNOWN'
    row['body']+=' You must hold 100 OP tokens.'
    assert build(row)['cost_status']=='FAIL'
