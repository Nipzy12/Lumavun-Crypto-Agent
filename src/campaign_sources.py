"""Documented campaign-specific adapters. Source labels are not payout promises."""
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

OPTIMISM_REPO = 'ethereum-optimism/ecosystem-contributions'
OPTIMISM_API = 'https://api.github.com/repos/' + OPTIMISM_REPO + '/issues?state=open&labels=Foundation%20Mission%20Request&per_page=30'
LABEL = 'Foundation Mission Request'


def optimism_binding(source, url):
    return source == OPTIMISM_API and bool(re.fullmatch(r'https://github\.com/ethereum-optimism/ecosystem-contributions/issues/[1-9][0-9]*', url or ''))


def sections(body):
    pattern = re.compile(r'^(?:\*\*([^\n]+?)\*\*[ \t]*:?[ \t]*|#{1,6}[ \t]+([^\n]+)\n)', re.M)
    matches = list(pattern.finditer(body))
    return {re.sub(r'[*:_?]', '', (m.group(1) or m.group(2))).strip().lower(): body[m.end():matches[i+1].start() if i+1<len(matches) else len(body)].strip()
            for i,m in enumerate(matches)}


def plain(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    return re.sub(r'[*_`\\]', '', text).strip()


def explicit_date(text):
    text = plain(text).split('\n', 1)[0].strip(' .:')
    text = re.sub(r'\s*\([^)]*\)\s*$', '', text).strip()
    for fmt in ('%Y-%m-%d', '%B %d %Y', '%B %d, %Y', '%b %d, %Y', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_optimism_missions(payload, spec):
    from sources import RawItem, SourceError
    if spec.url != OPTIMISM_API or not spec.official:
        raise SourceError(spec.id, 'Unapproved Optimism endpoint or ownership')
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise SourceError(spec.id, 'Expected GitHub repository issue array')
    result = []
    for row in rows[:30]:
        if not isinstance(row, dict):
            raise SourceError(spec.id, 'Invalid GitHub issue schema')
        labels = {l.get('name') for l in row.get('labels', []) if isinstance(l, dict)}
        url = row.get('html_url', '')
        if ('pull_request' in row or row.get('state') != 'open' or LABEL not in labels
            or row.get('repository_url') != 'https://api.github.com/repos/' + OPTIMISM_REPO
            or not optimism_binding(spec.url, url) or url.rsplit('/',1)[-1] != str(row.get('number'))):
            continue
        if not isinstance(row.get('body'), str) or not isinstance(row.get('title'), str):
            continue
        body = row['body'][:24000]
        parts = sections(body)
        reward = plain(parts.get('total grant amount', ''))
        program = bool(re.search(r'\b\d[\d,.]*(?:k|m)?\s*OP\b', reward, re.I))
        requirements = next((value for key,value in parts.items() if key.startswith('what is required to execute')), '')
        task_heading = re.search(r'^(?:\*\*|#{1,6}\s+)What is required to execute[^\n]*\n', body, re.M | re.I)
        if task_heading:
            requirements = body[task_heading.end():]
            end_heading = re.search(r'^(?:\*\*|#{1,6}\s+)(?:What are|How (?:will|should)|Application|Submit)[^\n]*', requirements, re.M | re.I)
            if end_heading:
                requirements = requirements[:end_heading.start()]
        tasks = [plain(line).lstrip('- ').strip() for line in requirements.splitlines()
                 if re.match(r'^\s*(?:[-*]|\d+[.)])\s+',line) and len(plain(line)) > 10][:20]
        # Paragraph requirements remain quoted; no generated actions.
        if not tasks and requirements:
            tasks = [plain(p).strip()[:500] for p in requirements.split('\n\n') if plain(p).strip()][:6]
        dates = {key: explicit_date(parts.get(key, '')) for key in ('start date','submit by','completion date')}
        end_dates = [dates[k] for k in ('submit by','completion date') if dates[k]]
        conditions = plain(body)
        conditional = bool(re.search(r'(?i)based on performance|selected|selection|must.{0,80}KYC|need to complete KYC|no guaranteed rewards',conditions))
        result.append(RawItem(row['title'], conditions, url, url, spec.url, spec.id,
            published=row.get('created_at'), entry_id=str(row['number']), metadata={
                'optimism_mission':True, 'tasks':tasks, 'reward':reward, 'program':program,
                'reward_guaranteed':False if conditional else None,
                'start_at':dates['start date'], 'end_at':min(end_dates) if end_dates else None,
                'date_evidence':{k:plain(parts.get(k,'')) for k in dates},
                'source_updated_at':row.get('updated_at'), 'author_association':row.get('author_association'),
                'truncated':len(row['body']) > 24000, 'body':conditions}))
    return result


def enrich_optimism(campaign, item, config):
    """Only authenticated-by-origin API records carrying the curated label enter here."""
    from quests import from_campaign, task_checklist
    m = item.metadata
    campaign.project = 'Optimism Collective'
    campaign.official_url = item.url
    campaign.source_verification_status = 'official_campaign_api'
    campaign.tasks = m['tasks']
    # A required attestation is a credential, not evidence of mandatory capital.
    # Only this explicit noun phrase is normalized; all payment/asset language
    # remains in the cost evaluator. Silence can still yield only UNKNOWN.
    cost_text = re.sub(r'\bmust hold (?:(?:one|any) of (?:the )?following )?attestations?\b',
                       'must have an attestation', m['body'], flags=re.I)
    if cost_text != m['body']:
        from analysis import evaluate_zero_capital
        cost = evaluate_zero_capital(campaign.title, cost_text, config)
        campaign.cost_status, campaign.zero_capital_ok = cost.status, cost.ok
        campaign.zero_capital_reason = cost.reason
        campaign.zero_capital_blockers = cost.blockers
        campaign.unknown_costs = cost.unknown_costs
    if m['truncated']:
        campaign.cost_status = 'UNKNOWN'
        campaign.zero_capital_ok = False
        campaign.zero_capital_reason = 'Campaign body exceeds extraction limit; complete cost evidence is unavailable'
    campaign.evidence_verified = bool(m['program'] and m['tasks'])
    campaign.opportunity_type = 'FREE_QUEST' if campaign.evidence_verified else 'UNVERIFIED'
    campaign.opportunity_reason = 'Foundation Mission Request label in the official repository; explicit grant allocation and task requirements. Selection and payout are conditional.'
    campaign.reward_text = 'Total program allocation (not individual earnings): ' + m['reward'] if m['reward'] else ''
    campaign.reward_status = 'stated' if m['program'] else 'unspecified'
    campaign.reward_note = 'An open GitHub issue does not establish current campaign availability or guaranteed payment'
    campaign.deadline = m['end_at']
    q = from_campaign(campaign, m['body'], {'start_at':m['start_at'], 'end_at':m['end_at']})
    q['tasks'] = task_checklist(m['tasks'], campaign.campaign_id)
    q['reward_type'] = 'TOKEN' if m['program'] else 'UNSPECIFIED'
    q['reward_guaranteed'] = m['reward_guaranteed']
    q['evidence_checks'] = {'official':True, 'organizer':True, 'reward':m['program'], 'participation':bool(m['tasks'])}
    q['eligibility_text'] = m['body']
    q['source_updated_at'] = m['source_updated_at']
    q['date_evidence'] = m['date_evidence']
    q['provenance'] = {'repository':OPTIMISM_REPO, 'curated_label':LABEL, 'author_association':m['author_association'],
                       'api_url':item.source_url, 'campaign_url':item.url}
    # Participation restrictions still come from explicit text and authorized checks.
    return q
