"""One persistent daily research batch; intended for a Railway cron service."""
import csv
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lead_agent.providers import Zyte
from lead_agent.runner import load_config, run
from lead_agent.store import Store


def cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def export_daily(store, directory, day, target, result):
    records = []
    for row in store.db.execute('SELECT payload FROM companies WHERE updated_at >= ? ORDER BY updated_at', (day,)):
        record = json.loads(row['payload'])
        if record.get('source_urls') and record.get('industry_match'):
            records.append(record)
    records = records[:target]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / (day + '.csv')
    temporary = path.with_suffix('.csv.tmp')
    fields = ['lead_id', 'business_name', 'website', 'industry', 'target_location', 'published_emails',
              'evidence_urls', 'qualification', 'rejection_reasons', 'outreach_eligible']
    with temporary.open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({k: cell(v) for k, v in {
                'lead_id': record['lead_id'], 'business_name': record.get('business_name'),
                'website': record['website'], 'industry': record.get('target_industry'),
                'target_location': record.get('target_location'),
                'published_emails': '; '.join(c['email'] for c in record.get('emails', [])),
                'evidence_urls': '; '.join(record['source_urls']),
                'qualification': record.get('qualification'),
                'rejection_reasons': '; '.join(record.get('rejection_reasons', [])),
                'outreach_eligible': False,
            }.items()})
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    summary = {**result, 'date_utc': day, 'target': target, 'listed': len(records),
               'shortfall': max(0, target-len(records)), 'list_path': str(path),
               'outreach_enabled': False, 'budget_basis': 'estimated; not an account-side billing limit'}
    report = path.with_suffix('.json')
    report_tmp = report.with_suffix('.json.tmp')
    report_tmp.write_text(json.dumps(summary, indent=2))
    os.chmod(report_tmp, 0o600)
    report_tmp.replace(report)
    return summary


def execute(config, store, provider, reports, day=None):
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
        result = run(config, store, provider)  # No Hunter/model/Sheets/campaign integration.
        summary = export_daily(store, reports, day, config['max_businesses'], result)
        if result.get('status') not in ('another_run_active', 'discovery_failed', 'failed'):
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
    if not os.environ.get('ZYTE_API_KEY'):
        raise SystemExit('ZYTE_API_KEY unavailable; no provider calls made')
    store = Store(path)
    provider = None
    try:
        provider = Zyte(os.environ['ZYTE_API_KEY'], store, {
            'max_calls': int(os.environ.get('LEAD_AGENT_MAX_DAILY_CALLS', '100')),
            'max_cost': float(os.environ.get('LEAD_AGENT_MAX_DAILY_COST_USD', '3')),
            'estimated_cost': float(os.environ.get('LEAD_AGENT_ESTIMATED_CALL_COST_USD', '0.03')),
        }, search_domain=os.environ.get('ZYTE_SEARCH_DOMAIN', 'google.com'))
        result = execute(config, store, provider, path.parent / 'reports')
        print(json.dumps(result), flush=True)
        return 1 if result['status'] in ('failed', 'discovery_failed', 'completed_with_errors') else 0
    finally:
        if provider:
            provider.session.close()
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
