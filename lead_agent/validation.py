"""Bounded live acceptance harness. Dry-run is the default; never sends or stages."""
import argparse
import json
import os
import uuid
from pathlib import Path
from lead_agent.store import Store
from lead_agent.providers import Zyte
from lead_agent.runner import run


def plan():
    return {
        'targets':[{'industry':'roofing','location':'New Martinsville, WV'},
                   {'industry':'plumbing','location':'New Martinsville, WV'}],
        'max_provider_attempts':6, 'estimated_cost_per_attempt_usd':0.01,
        'max_estimated_cost_usd':0.06, 'actual_price_guaranteed':False,
        'max_businesses_per_target':1, 'pages_per_business':1,
        'hunter_enabled':False,'model_enabled':False,'sheet_writes':False,
        'outreach_enabled':False,'deployment_enabled':False,
    }


def evaluate(store, outcomes):
    companies=store.db.execute('SELECT payload,stage FROM companies').fetchall()
    supported=0; attributed=0; websites=0; errors=0
    reasons={}
    for row in companies:
        record=json.loads(row['payload'])
        supported+=bool(record.get('business_name') and record.get('business_name_source_url'))
        attributed+=bool(record.get('fit_evidence'))
        websites+=bool(record.get('source_urls'))
        errors+=len(record.get('collection_errors',[]))
        for reason in record.get('rejection_reasons',[]): reasons[reason]=reasons.get(reason,0)+1
    return {'target_runs':outcomes,'companies':len(companies),'supported_company_names':supported,
            'businesses_with_pages':websites,'businesses_with_quoted_evidence':attributed,
            'collection_errors':errors,'rejection_reasons':reasons,'metrics':store.metrics(),
            'live_contact_verification_tested':False,'live_sheets_tested':False,
            'campaign_modified':False,'production_ready':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Six-attempt research-only Zyte validation; no outreach')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--allow-provider-charges',action='store_true')
    parser.add_argument('--output-dir',default='data/live-validation')
    args=parser.parse_args(argv)
    test_plan=plan()
    if not args.execute:
        print(json.dumps({'status':'dry_run','network_calls':0,'database_writes':0,'plan':test_plan},indent=2))
        return 0
    if not args.allow_provider_charges:
        print(json.dumps({'status':'blocked','reason':'Explicit paid-provider approval required','network_calls':0}))
        return 2
    if not os.environ.get('ZYTE_API_KEY'):
        print(json.dumps({'status':'blocked','reason':'ZYTE_API_KEY must be securely configured in this execution environment','network_calls':0}))
        return 2
    # Unique local state prevents old checkpoints from masquerading as live results.
    root=Path(args.output_dir)/uuid.uuid4().hex
    root.mkdir(parents=True,exist_ok=False)
    store=Store(root/'research.sqlite3')
    budget={'max_calls':6,'max_cost':0.06,'estimated_cost':0.01}
    provider=Zyte(os.environ['ZYTE_API_KEY'],store,budget,
                  search_domain=os.environ.get('ZYTE_SEARCH_DOMAIN','google.com'))
    outcomes=[]
    try:
        for target in test_plan['targets']:
            result=run({'industries':[target['industry']],'locations':[target['location']],
                        'max_businesses':1,'pages_per_site':1,'max_contacts_per_business':1,
                        'max_runtime_seconds':120},store,provider)
            outcomes.append({'industry':target['industry'],**result})
        report=evaluate(store,outcomes)
        report['plan']=test_plan
        good=(all(x['status']=='completed' and x['researched']==1 and x['errors']==0 for x in outcomes)
              and report['supported_company_names']==2 and report['businesses_with_pages']==2
              and report['businesses_with_quoted_evidence']==2 and report['collection_errors']==0)
        report['status']='research_smoke_passed' if good else 'incomplete_or_rejected_sample'
        # Research records contain published personal data; summaries never print contacts.
        report_path=root/'report.json'
        report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'status':report['status'],'report':str(report_path),'companies':report['companies'],
                          'outreach_enabled':False,'production_ready':False}))
        return 0 if good else 1
    finally:
        provider.session.close()
        store.close()


if __name__=='__main__': raise SystemExit(main())
