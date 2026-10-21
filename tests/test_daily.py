import csv
import json
from unittest.mock import Mock, patch
import pytest
from lead_agent.daily import execute, export_daily, main
from lead_agent.store import Store
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
    store.save_company(r, 'rejected')
    report = export_daily(store, tmp_path, '2000-01-01', 25, {'status':'completed'})
    rows = list(csv.DictReader(open(report['list_path'])))
    assert len(rows) == 1 and rows[0]['business_name'].startswith("'=")
    assert rows[0]['outreach_eligible'] == 'False'
    assert rows[0]['evidence_urls'] == 'https://roofbusiness.com/'
    assert report['shortfall'] == 24
    assert json.load(open(tmp_path/'2000-01-01.json'))['listed'] == 1
    store.close()


def test_daily_does_not_pad_with_unresearched_matches(tmp_path):
    store = Store(':memory:')
    r = record(); r['source_urls'] = []
    store.save_company(r, 'rejected')
    assert export_daily(store, tmp_path, '2000-01-01', 25, {})['listed'] == 0
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


def test_railway_worker_uses_its_own_image_without_web_healthcheck():
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parents[1]/'railway.worker.json').read_text())
    assert config['build']['dockerfilePath'] == 'Dockerfile.worker'
    assert config['deploy']['startCommand'] == 'python -m lead_agent.daily'
    assert config['deploy']['restartPolicyType'] == 'NEVER'
    assert 'healthcheckPath' not in config['deploy']
