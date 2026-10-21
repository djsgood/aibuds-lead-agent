"""Explicit local approval queue; no live campaign importer or sender."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from lead_agent.research import strict_qualification
from lead_agent.store import now


def digest(payload): return hashlib.sha256(payload.encode()).hexdigest()


def prepare(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS review_approvals(company_id TEXT PRIMARY KEY REFERENCES companies(id), payload_hash TEXT NOT NULL, reviewer TEXT NOT NULL, approved_at TEXT NOT NULL)')


def require_suppression_sync(store):
    checkpoint = store.get_checkpoint('campaign_suppression_sync')
    if not checkpoint: raise ValueError('Fresh read-only campaign snapshot sync required')
    for field, hours in (('at',1), ('source_modified_at',24)):
        age = datetime.now(timezone.utc) - datetime.fromisoformat(checkpoint[field])
        if not timedelta(0) <= age <= timedelta(hours=hours):
            raise ValueError('Campaign suppression coverage expired; handoff blocked')


def eligible(store, payload):
    record = json.loads(payload)
    if store.blocked(record['website'], [e['email'] for e in record.get('emails', [])]):
        raise ValueError('Suppressed prospect; handoff blocked')
    if strict_qualification(record)['qualification'] != 'qualified':
        raise ValueError('Qualification or fresh verification required')
    return record


def approve(store, company_id, reviewer):
    if not isinstance(reviewer,str) or not reviewer.strip(): raise ValueError('Reviewer identity required')
    prepare(store)
    with store.transaction():
        require_suppression_sync(store)
        row = store.db.execute('SELECT payload FROM companies WHERE id=?', (company_id,)).fetchone()
        if not row: raise ValueError('Unknown prospect')
        eligible(store, row['payload'])
        store.db.execute('INSERT OR REPLACE INTO review_approvals VALUES (?,?,?,?)',
                         (company_id,digest(row['payload']),reviewer.strip(),now()))
    return {'status':'approved_locally', 'lead_id':company_id, 'automatic_handoff':False}


def handoff_records(store):
    prepare(store)
    require_suppression_sync(store)
    rows = store.db.execute('SELECT c.id,c.payload,a.payload_hash FROM companies c JOIN review_approvals a ON c.id=a.company_id').fetchall()
    records, blocked = [], 0
    for row in rows:
        try:
            if digest(row['payload']) != row['payload_hash']: raise ValueError('Approval version changed')
            record = eligible(store,row['payload'])
            contact = record['preferred_contact']
            records.append({'lead_id':row['id'], 'company_name':record['business_name'],
                'website_url':record['website'], 'contact_email':contact['email'],
                'first_name':contact['name'].split()[0], 'evidence':record['fit_evidence'],
                'verification':{k:contact[k] for k in ('verification_method','checked_at','provider_verified_at')},
                'outreach_approved':True, 'campaign_import_enabled':False})
        except ValueError: blocked += 1
    return {'records':records, 'blocked':blocked, 'automatic_handoff':False}
