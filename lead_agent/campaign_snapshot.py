"""Read-only campaign history ingestion. Never import or execute campaign code."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from aibuds_crawler.discovery import domain_key, business_url
from aibuds_crawler.spiders.business_contacts import published_email
from lead_agent.store import now


def sync_campaign_snapshot(store, path, *, max_age_hours=24):
    path = Path(path)
    stat = path.stat()
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    if not timedelta(0) <= age <= timedelta(hours=max_age_hours):
        raise ValueError('Campaign snapshot is stale or future-dated; handoff blocked')
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Campaign snapshot must contain verified nonempty history')
    entries, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid campaign history row')
        if not isinstance(row.get('contact_email'),str): raise ValueError('Invalid campaign email')
        email = published_email(row['contact_email'])
        if not email or email in seen or not str(row.get('company_name') or '').strip():
            raise ValueError('Invalid or duplicate campaign identity')
        seen.add(email)
        for field in ('started_at','day1_sent_at','day5_sent_at','day7_sent_at','replied_at','bounced_at','opted_out_at','stopped_at','pending_at'):
            value = row.get(field, '')
            if not isinstance(value, str): raise ValueError('Invalid campaign timestamp')
            if value and datetime.fromisoformat(value).tzinfo is None:
                raise ValueError('Campaign timestamps require timezone')
        reason = 'existing_campaign_contact'
        if row.get('opted_out_at'): reason = 'campaign_opt_out'
        elif row.get('replied_at'): reason = 'campaign_reply'
        elif row.get('bounced_at'): reason = 'campaign_bounce'
        elif row.get('pending_stage'): reason = 'campaign_uncertain_send'
        elif row.get('day1_sent_at'): reason = 'campaign_contacted'
        website = row.get('website_url') or ''
        if not isinstance(website, str): raise ValueError('Invalid campaign website')
        if website:
            if not business_url(website): raise ValueError('Invalid campaign domain')
            domain_key(website)
        entries.append((email, website, reason))
    # Validate the whole snapshot before importing any suppression.
    with store.transaction():
        for email, website, reason in entries:
            store.suppress('email', email, reason)
            if website: store.suppress('domain', website, reason)
        store.checkpoint('campaign_suppression_sync', {'at':now(), 'rows':len(entries),
                         'source_modified_at':datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                         'coverage':'configured_snapshot_only'})
    return {'status':'synced_read_only', 'rows':len(entries), 'campaign_modified':False}
