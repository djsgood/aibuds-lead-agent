import csv
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import pytest
from lead_agent.daily import execute, export_daily, main, target_area_evidence
from lead_agent.providers import ProviderFailure
from lead_agent.store import BudgetExceeded, Store
from aibuds_crawler.discovery import domain_key
from test_persistent_agent import record


def test_daily_runs_once_and_survives_restart(tmp_path):
    path = tmp_path/'db.sqlite3'
    store = Store(path)
    with patch('lead_agent.daily.run', return_value={'status':'completed', 'outreach_enabled':False}) as run:
        result = execute({'max_businesses':25}, store, Mock(), tmp_path/'reports', day='2026-10-21')
        assert result['shortfall'] == 25
        store.close()
        store = Store(path)
        assert execute({'max_businesses':25}, store, Mock(), tmp_path/'reports', day='2026-10-21')['already_ran_today']
        run.assert_called_once()
        execute({'max_businesses':25}, store, Mock(), tmp_path/'reports', day='2026-10-22')
        assert run.call_count == 2
    store.close()


def test_daily_csv_sources_and_unverified_status(tmp_path):
    store = Store(':memory:')
    r = record(); r['business_name'] = '=malicious()'
    r['emails'][0]['verification_status'] = 'unverified'
    r['research_documents'] = [{'kind':'business_website','source_url':r['website'],'text':'Roofing services in New Martinsville, WV. Request a quote.'}]
    store.save_company(r, 'rejected')
    report = export_daily(store, tmp_path, datetime.now(timezone.utc).date().isoformat(), 25, {'status':'completed'})
    rows = list(csv.DictReader(open(report['list_path'])))
    assert len(rows) == 1 and rows[0]['business_name'].startswith("'=")
    assert rows[0]['outreach_eligible'] == 'False'
    assert rows[0]['evidence_urls'] == 'https://roofbusiness.com/'
    assert report['shortfall'] == 24
    assert json.load(open(tmp_path/(datetime.now(timezone.utc).date().isoformat()+'.json')))['listed'] == 1
    store.close()


def test_daily_does_not_pad_with_unresearched_matches(tmp_path):
    store = Store(':memory:')
    r = record(); r['source_urls'] = []
    store.save_company(r, 'rejected')
    assert export_daily(store, tmp_path, datetime.now(timezone.utc).date().isoformat(), 25, {})['listed'] == 0
    store.close()


def test_concurrent_daily_run_does_not_release_other_lease(tmp_path):
    store = Store(':memory:')
    store.claim('daily','other')
    assert execute({'max_businesses':25},store,Mock(),tmp_path)['status'] == 'another_daily_run_active'
    assert not store.claim('daily','third')
    store.close()


@pytest.mark.parametrize('settings', [{}, {'ENABLE_SCHEDULE':'true','LEAD_AGENT_DB':'/tmp/wrong'},
                                      {'ENABLE_SCHEDULE':'true','LEAD_AGENT_DB':'/data/../tmp/wrong'}])
def test_disabled_or_nonpersistent_worker_makes_no_calls(settings):
    with patch.dict('os.environ', settings, clear=True), patch('lead_agent.daily.Store') as store:
        with pytest.raises(SystemExit): main()
        store.assert_not_called()


def test_provider_failure_can_resume_after_configuration_repair(tmp_path):
    store = Store(':memory:')
    with patch('lead_agent.daily.run', return_value={'status':'discovery_failed'}):
        execute({'max_businesses':25}, store, Mock(), tmp_path, day='2026-10-21')
    assert store.get_checkpoint('daily:2026-10-21') is None
    with patch('lead_agent.daily.run', return_value={'status':'completed'}) as run:
        execute({'max_businesses':25}, store, Mock(), tmp_path, day='2026-10-21')
        run.assert_called_once()
    store.close()


def test_completed_with_errors_resumes_same_day_without_repeating_work_or_budget(tmp_path):
    store = Store(tmp_path/'resume.sqlite3')

    class Provider:
        def __init__(self):
            self.searches = 0
            self.fetches = {}
            self.failures_left = 2

        def charge(self):
            store.reserve_call('zyte', max_calls=7, max_cost=1, estimated_cost=.1)

        def search(self, query):
            self.charge()
            self.searches += 1
            return {'source_url':'https://google.com/search?q=roofing', 'retrieved_at':'2026-10-21T00:00:00Z',
                    'results':[{'url':'https://goodroof.com/'}, {'url':'https://retryroof.com/'}]}

        def fetch(self, url):
            self.charge()
            self.fetches[url] = self.fetches.get(url, 0) + 1
            if url == 'https://retryroof.com/robots.txt' and self.failures_left:
                self.failures_left -= 1
                raise ProviderFailure('Website HTTP 503')
            if url.endswith('/robots.txt'):
                return 'User-agent: *\nAllow: /'
            name = 'Good' if 'goodroof' in url else 'Retry'
            return f'''<body>Roofing in New Martinsville, WV. Request a quote.
            <script type="application/ld+json">{{"@type":"RoofingContractor","name":"{name} Roofing","address":{{"addressCountry":"US"}}}}</script></body>'''

    provider = Provider()
    config = {'industries':['roofing'], 'locations':['New Martinsville, WV'], 'max_businesses':2,
              'pages_per_site':1, 'daily_target':2}
    day = datetime.now(timezone.utc).date().isoformat()

    first = execute(config, store, provider, tmp_path/'reports', day=day)
    assert first['status'] == 'completed_with_errors'
    assert store.get_checkpoint('daily:' + day) is None
    assert store.metrics()['companies'] == 1
    assert store.metrics()['budgets'][0]['calls'] == 4

    retry_key = 'research:' + domain_key('https://retryroof.com/')
    assert store.db.execute('SELECT failures FROM jobs WHERE job_key=?', (retry_key,)).fetchone()[0] == 1
    store.db.execute('UPDATE jobs SET retry_at=0 WHERE job_key=?', (retry_key,))
    second = execute(config, store, provider, tmp_path/'reports', day=day)
    assert second['status'] == 'completed_with_errors'
    assert store.get_checkpoint('daily:' + day) is None
    assert store.metrics()['companies'] == 1
    assert store.metrics()['budgets'][0]['calls'] == 5
    assert store.db.execute('SELECT failures FROM jobs WHERE job_key=?', (retry_key,)).fetchone()[0] == 2
    store.db.execute('UPDATE jobs SET retry_at=0 WHERE job_key=?', (retry_key,))

    third = execute(config, store, provider, tmp_path/'reports', day=day)
    assert third['status'] == 'completed'
    assert third['listed'] == 2
    assert store.metrics()['companies'] == 2
    assert store.metrics()['budgets'][0]['calls'] == 7
    assert provider.searches == 1
    assert provider.fetches['https://goodroof.com/robots.txt'] == 1
    assert provider.fetches['https://goodroof.com/'] == 1
    assert store.db.execute('SELECT failures FROM jobs WHERE job_key=?', (retry_key,)).fetchone()[0] == 2
    with pytest.raises(BudgetExceeded):
        store.reserve_call('zyte', max_calls=7, max_cost=1, estimated_cost=.1)
    store.close()


def test_railway_worker_uses_its_own_image_without_web_healthcheck():
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parents[1]/'railway.worker.json').read_text())
    assert config['build']['dockerfilePath'] == 'Dockerfile.worker'
    assert config['deploy']['startCommand'] == 'python -m lead_agent.daily'
    assert config['deploy']['restartPolicyType'] == 'NEVER'
    assert 'healthcheckPath' not in config['deploy']


def test_daily_rechecks_completion_after_claim(tmp_path):
    store = Store(':memory:')
    original = store.claim
    def claim(*args, **kwargs):
        store.checkpoint('daily:2026-10-21', {'status':'completed'})
        return original(*args, **kwargs)
    with patch.object(store, 'claim', side_effect=claim), patch('lead_agent.daily.run') as run:
        assert execute({'max_businesses':25},store,Mock(),tmp_path,day='2026-10-21')['already_ran_today']
        run.assert_not_called()
    store.close()


@pytest.mark.parametrize('text', ['Roofing in New Martinsville, OH', 'Roofing in Idaho', 'New Martinsville WVille'])
def test_wrong_state_or_missing_locality_is_not_a_local_lead(text):
    r=record(); r['research_documents']=[{'kind':'business_website','source_url':r['website'],'text':text}]
    assert target_area_evidence(r) is None


def test_report_does_not_include_another_days_records(tmp_path):
    store=Store(':memory:'); r=record(); r['research_documents']=[{'kind':'business_website','source_url':r['website'],'text':'New Martinsville, WV'}]
    store.save_company(r, 'rejected')
    assert export_daily(store,tmp_path,'2000-01-01',25,{})['listed']==0
    store.close()


def test_local_match_can_be_any_owner_approved_city():
    r=record(); r['research_documents']=[{'kind':'business_website','source_url':r['website'],'text':'Roofing in Wheeling, WV'}]
    locations=['New Martinsville, WV', 'Wheeling, WV']
    assert target_area_evidence(r,locations)['target_location']=='Wheeling, WV'
    assert target_area_evidence(r,['Washington, PA']) is None
