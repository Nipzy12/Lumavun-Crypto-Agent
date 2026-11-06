"""Adapter contract tests use the published Galxe schema and synthetic data."""
from copy import deepcopy
import pytest
from config_loader import load_config
from quest_adapters import GALXE_ENDPOINT, galxe_discover, discover_quests, eligibility_from_groups, load_quest_config
from sources import reset_network_caches
from discovery import SourceHealth
from test_quests_v14 import campaign

DATA = {'id': 'synthetic-id', 'name': 'Example Points Quest', 'type': 'Points', 'status': 'Active',
        'description': 'Join this points program to earn points. Participation is free. Open to everyone.',
        'startTime': '2026-11-01', 'endTime': '2026-12-01', 'cap': 100, 'participantsCount': 3,
        'loyaltyPoints': 100, 'gasType': 'Gasless', 'space': {'id': 'synthetic-space', 'name': 'Example'}, 'chain': 'SOLANA'}
SETTINGS = {'enabled': True, 'quests': [{'id': 'synthetic-id', 'official_url': 'https://app.galxe.com/quest/synthetic-id', 'reviewed': True}]}

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv('GALXE_ACCESS_TOKEN', raising=False)
    monkeypatch.setattr('quest_adapters._respect_rate_limit', lambda *a: None)
    reset_network_caches()


def test_no_token_no_network(requests_mock):
    campaigns, health = galxe_discover(SETTINGS, load_config())
    assert not campaigns and health[0]['status'] == 'AUTH_REQUIRED'
    assert not requests_mock.called


def test_not_enabled_even_with_token(monkeypatch, requests_mock):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    assert galxe_discover({}, load_config())[1][0]['status'] == 'DISABLED'
    assert not requests_mock.called


def test_authorized_documented_query_and_unknown_eligibility(monkeypatch, requests_mock):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    requests_mock.post(GALXE_ENDPOINT, json={'data': {'quest': DATA}})
    found, health = galxe_discover(SETTINGS, load_config())
    assert health[0]['status'] == 'OK'
    assert found[0].evidence_verified and found[0].cost_status == 'PASS'
    assert found[0].quest_intelligence['eligibility_status'] == 'UNKNOWN'
    request = requests_mock.last_request
    assert request.headers['access-token'] == 'synthetic-secret'
    assert request.json()['variables'] == {'id': 'synthetic-id'}
    assert 'mutation' not in request.json()['query']

@pytest.mark.parametrize('gas,description,expected', [
    ('Gas', DATA['description'], 'FAIL'),
    ('Gasless', 'Join the points program to earn points.', 'UNKNOWN'),
    ('Gasless', 'Join the points program. You must deposit 1 SOL.', 'FAIL'),
    ('Unknown', DATA['description'], 'UNKNOWN'),
])
def test_galxe_never_infers_free_tasks_from_gasless(monkeypatch, requests_mock, gas, description, expected):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    data = {**DATA, 'gasType': gas, 'description': description}
    requests_mock.post(GALXE_ENDPOINT, json={'data': {'quest': data}})
    assert galxe_discover(SETTINGS, load_config())[0][0].cost_status == expected

@pytest.mark.parametrize('http,expected', [(401, 'AUTH_REQUIRED'), (403, 'AUTH_REQUIRED'), (429, 'RATE_LIMITED'), (500, 'UNAVAILABLE'), (302, 'UNSUPPORTED')])
def test_failure_does_not_break_scan(monkeypatch, requests_mock, http, expected):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    requests_mock.post(GALXE_ENDPOINT, status_code=http)
    settings = deepcopy(SETTINGS)
    settings['quests'] *= 2
    found, health = galxe_discover(settings, load_config())
    assert not found and health[0]['status'] == expected
    if http in (401, 403, 429):
        assert requests_mock.call_count == 1


def test_graphql_errors_are_redacted(monkeypatch, requests_mock):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    requests_mock.post(GALXE_ENDPOINT, json={'errors': [{'message': 'synthetic-secret', 'extensions': {'category': 'AUTHENTICATION'}}], 'data': {'quest': DATA}})
    found, health = galxe_discover(SETTINGS, load_config())
    assert not found and health[0]['status'] == 'AUTH_REQUIRED'
    assert 'synthetic-secret' not in str(health)


def test_optional_read_only_eligibility(monkeypatch, requests_mock):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    settings = {**SETTINGS, 'eligibility_checks': True, 'eligibility_address': 'public-address'}
    eligibility = {'id': DATA['id'], 'credentialGroups': [{'conditionRelation': 'ALL', 'conditions': [{'eligible': True}]}]}
    requests_mock.post(GALXE_ENDPOINT, [{'json': {'data': {'quest': DATA}}}, {'json': {'data': {'quest': eligibility}}}])
    found, health = galxe_discover(settings, load_config())
    assert found[0].quest_intelligence['eligibility_status'] == 'PASS'
    assert requests_mock.last_request.json()['variables']['address'] == 'public-address'
    assert all('mutation' not in r.json()['query'] for r in requests_mock.request_history)

@pytest.mark.parametrize('groups,expected', [
    ([], 'UNKNOWN'), (None, 'UNKNOWN'),
    ([{'conditionRelation': 'ALL', 'conditions': [{'eligible': True}, {'eligible': False}]}], 'FAIL'),
    ([{'conditionRelation': 'ANY', 'conditions': [{'eligible': True}, {'eligible': False}]}], 'PASS'),
    ([{'conditionRelation': 'ALL', 'conditions': [{'eligible': 'true'}]}], 'UNKNOWN'),
    ([{'conditionRelation': 'OTHER', 'conditions': [{'eligible': True}]}], 'UNKNOWN'),
])
def test_eligibility_is_three_state(groups, expected):
    assert eligibility_from_groups(groups) == expected


def test_unsupported_platforms_and_manual_urls_do_not_scrape(requests_mock):
    health = [SourceHealth(url='https://example.test/feed', reachable=True)]
    settings = {'other_platforms': ['unreviewed-platform'], 'manual_campaigns': [{'official_url': 'https://example.test/unknown', 'official_feed_id': 'example'}]}
    found, adapters = discover_quests([], health, load_config(), settings)
    assert not found and not requests_mock.called
    assert {a['status'] for a in adapters} == {'OK', 'AUTH_REQUIRED', 'UNSUPPORTED'}


def test_manual_url_verified_by_official_feed():
    c = campaign()
    settings = {'manual_campaigns': [{'official_url': c.official_url, 'official_feed_id': c.source_id}]}
    found, diagnostics = discover_quests([c], [SourceHealth(c.source, True)], load_config(), settings)
    assert len(found) == 1
    assert next(d for d in diagnostics if d['adapter'] == 'manual_url')['status'] == 'OK'


def test_unreviewed_public_feed_is_not_requested(requests_mock):
    settings = {'public_feeds': [{'url': 'https://unreviewed.example/feed'}]}
    found, health = discover_quests([], [], load_config(), settings)
    assert not requests_mock.called
    assert health[-1]['status'] == 'UNSUPPORTED'


def test_bad_configuration_is_reported(tmp_path):
    path = tmp_path / 'quest.yaml'
    path.write_text('galxe: [wrong]\n')
    settings = load_quest_config(path)
    assert settings.get('configuration_error')
    assert discover_quests([], [], load_config(), settings)[1][-1]['status'] == 'UNSUPPORTED'


def test_reviewed_public_feed_adapter_works(requests_mock):
    from test_quests_v14 import TEXT
    requests_mock.get('https://example.test/robots.txt', text='User-agent: *\nAllow: /')
    requests_mock.get('https://example.test/feed', text=f'<rss version="2.0"><channel><item><title>Example announces Points campaign</title><link>https://example.test/quest/1</link><description>{TEXT}</description></item></channel></rss>')
    settings = {'public_feeds': [{'id': 'example', 'url': 'https://example.test/feed', 'documentation_url': 'https://example.test/docs/feed', 'reviewed_public_access': True}]}
    found, health = discover_quests([], [], load_config(), settings)
    assert len(found) == 1 and found[0].evidence_verified
    assert health[-1]['status'] == 'OK'


def test_malformed_api_response_is_unsupported(monkeypatch, requests_mock):
    monkeypatch.setenv('GALXE_ACCESS_TOKEN', 'synthetic-secret')
    requests_mock.post(GALXE_ENDPOINT, json={'data': {'quest': {**DATA, 'id': 'wrong-id'}}})
    found, health = galxe_discover(SETTINGS, load_config())
    assert not found and health[0]['status'] == 'UNSUPPORTED'
