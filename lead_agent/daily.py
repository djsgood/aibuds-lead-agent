"""One persistent daily research batch; intended for a Railway cron service."""
import csv
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lead_agent.providers import Firecrawl, Hunter
from lead_agent.research import strict_qualification
from lead_agent.handoff import stage
from lead_agent.runner import load_config, run
from lead_agent.store import Store


def cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def target_area_evidence(record, locations=None):
    if locations is not None:
        for location in dict.fromkeys(locations):
            evidence = target_area_evidence({**record, 'target_location': location})
            if evidence:
                return evidence
        return None
    city, separator, state = str(record.get('target_location', '')).rpartition(',')
    city, state = city.strip(), state.strip().upper()
    states = {'WV':'West Virginia', 'OH':'Ohio', 'PA':'Pennsylvania'}
    if not separator or not city or state not in states:
        return None
    pattern = re.compile(r'(?<!\w)' + re.escape(city) + r'[\s,]+(?:' +
                         re.escape(state) + '|' + re.escape(states[state]) + r')(?![A-Za-z])', re.I)
    for document in record.get('research_documents', []):
        if document.get('kind') != 'business_website' or document.get('source_url') not in record.get('source_urls', []):
            continue
        text = str(document.get('text', ''))
        match = pattern.search(text)
        if match:
            return {'source_url': document['source_url'], 'target_location': record['target_location'],
                    'quote': text[max(0, match.start()-50):match.end()+70],
                    'classification':'website_mentions_target_area; headquarters not independently verified'}
    return None


def export_daily(store, directory, day, target, result, locations=None):
    records = []
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    for row in store.db.execute('SELECT payload FROM companies WHERE updated_at >= ? AND updated_at < ? ORDER BY updated_at',
                                (start.isoformat(), end.isoformat())):
        record = json.loads(row['payload'])
        area = target_area_evidence(record, locations)
        if record.get('qualification') == 'qualified' and strict_qualification(record)['qualification'] == 'qualified' and not store.blocked(record['website'], [e['email'] for e in record.get('emails', [])]) and area:
            records.append({**record, 'target_area_evidence': area})
    records = records[:target]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / (day + '.csv')
    temporary = path.with_suffix('.csv.tmp')
    fields = ['lead_id', 'business_name', 'website', 'industry', 'target_location', 'published_emails',
              'evidence_urls', 'location_evidence_url', 'location_quote', 'qualification', 'rejection_reasons', 'outreach_eligible']
    with temporary.open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({k: cell(v) for k, v in {
                'lead_id': record['lead_id'], 'business_name': record.get('business_name'),
                'website': record['website'], 'industry': record.get('target_industry'),
                'target_location': record['target_area_evidence']['target_location'],
                'published_emails': '; '.join(c['email'] for c in record.get('emails', [])),
                'evidence_urls': '; '.join(record['source_urls']),
                'location_evidence_url': record['target_area_evidence']['source_url'],
                'location_quote': record['target_area_evidence']['quote'],
                'qualification': record.get('qualification'),
                'rejection_reasons': '; '.join(record.get('rejection_reasons', [])),
                'outreach_eligible': False,
            }.items()})
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    summary = {**result, 'date_utc': day, 'target': target, 'listed': len(records),
               'shortfall': max(0, target-len(records)), 'list_path': str(path),
               'outreach_enabled': False, 'budget_basis': 'estimated; not an account-side billing limit',
               'usage': [dict(row) for row in store.db.execute('SELECT provider,calls,estimated_cost FROM budgets WHERE day=?', (day,))]}
    report = path.with_suffix('.json')
    report_tmp = report.with_suffix('.json.tmp')
    report_tmp.write_text(json.dumps(summary, indent=2))
    os.chmod(report_tmp, 0o600)
    report_tmp.replace(report)
    return summary


def execute(config, store, provider, reports, day=None, *, hunter=None, sheet_stage=None):
    day = day or datetime.now(timezone.utc).date().isoformat()
    checkpoint = 'daily:' + day
    previous = store.get_checkpoint(checkpoint)
    if previous:
        return {**previous, 'already_ran_today': True}
    owner = uuid.uuid4().hex
    if not store.claim('daily', owner, seconds=900):
        return {'status': 'another_daily_run_active', 'outreach_enabled': False}
    try:
        previous = store.get_checkpoint(checkpoint)
        if previous:
            return {**previous, 'already_ran_today': True}
        result = run(config, store, provider, hunter=hunter)
        if sheet_stage is not None:
            result['sheet_delivery'] = sheet_stage()
            if result['sheet_delivery'].get('uncertain'):
                result['status'] = 'completed_with_errors'
        summary = export_daily(store, reports, day, config.get('daily_target', config['max_businesses']), result, locations=config.get('locations'))
        if result.get('status') not in ('another_run_active', 'discovery_failed', 'failed', 'completed_with_errors'):
            store.checkpoint(checkpoint, summary)
        return summary
    finally:
        store.release('daily', owner)


def main():
    if os.environ.get('ENABLE_SCHEDULE', 'false').lower() != 'true':
        raise SystemExit('Scheduling disabled')
    path = Path(os.environ.get('LEAD_AGENT_DB', '')).resolve()
    if not path.is_absolute() or not path.is_relative_to('/data') or path == Path('/data'):
        raise SystemExit('Daily worker requires a database below persistent /data')
    config = load_config(os.environ.get('LEAD_AGENT_CONFIG', 'config/targets.daily.json'))
    required = ('FIRECRAWL_API_KEY', 'HUNTER_API_KEY', 'GOOGLE_SERVICE_ACCOUNT_JSON', 'LEADS_SPREADSHEET_ID')
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit('Missing configuration: ' + ', '.join(missing))
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']),
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    os.umask(0o077)
    store = Store(path)
    provider = hunter = None
    try:
        os.chmod(path, 0o600)
        provider = Firecrawl(os.environ['FIRECRAWL_API_KEY'], store, {
            'max_calls': int(os.environ.get('LEAD_AGENT_MAX_DAILY_CALLS', '100')),
            'max_cost': float(os.environ.get('LEAD_AGENT_MAX_DAILY_COST_USD', '3')),
            'estimated_cost': float(os.environ.get('LEAD_AGENT_ESTIMATED_CALL_COST_USD', '0.03')),
        })
        hunter = Hunter(os.environ['HUNTER_API_KEY'], store, {
            'max_calls': int(os.environ.get('HUNTER_MAX_DAILY_CALLS', '50')),
            'max_cost': float(os.environ.get('HUNTER_MAX_DAILY_COST_USD', '3')),
            'estimated_cost': float(os.environ.get('HUNTER_ESTIMATED_CALL_COST_USD', '0.06')),
        })
        from lead_agent.handoff import sync_existing
        with AuthorizedSession(creds) as session:
            spreadsheet = os.environ['LEADS_SPREADSHEET_ID']
            tab = os.environ.get('LEADS_SHEET_TAB', 'Lead Review')
            if tab.strip().lower() != 'lead review':
                raise SystemExit('Only Lead Review staging is permitted')
            sync_existing(store, session, spreadsheet)
            result = execute(config, store, provider, path.parent / 'reports', hunter=hunter,
                             sheet_stage=lambda: stage(store, session, spreadsheet, tab))
        print(json.dumps(result), flush=True)
        return 1 if result['status'] in ('failed', 'discovery_failed', 'completed_with_errors') else 0
    finally:
        for client in (provider, hunter):
            if client:
                client.session.close()
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
