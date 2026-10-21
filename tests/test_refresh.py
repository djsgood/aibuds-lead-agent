"""Refresh and expiry regressions, using synthetic providers and temporary state."""
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from lead_agent.handoff import stage, LIVE_HEADERS
from lead_agent.research import fresh_verification, strict_qualification
from lead_agent.runner import run, load_config
from lead_agent.store import Store
from aibuds_crawler.sheets import HEADERS
from test_persistent_agent import record, FakeZyte, FakeHunter


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / 'refresh.sqlite3')
    yield store
    store.close()


def cutoff():
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def seed_old(store, row=None, stage_='rejected'):
    row = row or record()
    store.save_company(row, stage_)
    store.db.execute('UPDATE companies SET updated_at=?', ('2020-01-01T00:00:00+00:00',))
    return row


def test_explicit_refresh_keeps_immutable_prior_payload(db):
    original = seed_old(db)
    generation = db.refresh_version(original['website'], cutoff())
    updated = copy.deepcopy(original)
    updated['business_name'] = 'Updated supported claim'
    updated['outreach_approved'] = True
    assert db.save_company(updated, 'rejected', refresh_before=cutoff(), expected_version=generation) == 'saved'
    versions = db.db.execute('SELECT * FROM company_versions ORDER BY version').fetchall()
    assert len(versions) == 2
    assert json.loads(versions[0]['payload'])['business_name'] == original['business_name']
    assert json.loads(versions[1]['payload'])['business_name'] == 'Updated supported claim'
    assert json.loads(versions[1]['payload'])['outreach_approved'] is False
    assert db.metrics()['companies'] == 1
    assert db.refresh_version(updated['website'], cutoff()) is None


def test_old_schema_gets_snapshot_on_first_refresh(tmp_path):
    store = Store(tmp_path / 'old.sqlite3')
    original = seed_old(store)
    # Simulate the pre-upgrade schema without version history.
    store.db.execute('DELETE FROM company_versions')
    assert store.save_company(original, 'rejected', refresh_before=cutoff(), expected_version='2020-01-01T00:00:00+00:00') == 'saved'
    assert store.db.execute('SELECT count(*) FROM company_versions').fetchone()[0] == 2
    store.close()


@pytest.mark.parametrize('protection', ['qualified', 'reviewed', 'approved', 'outbox', 'suppressed_email', 'suppressed_domain'])
def test_refresh_cannot_bypass_review_or_suppression(db, protection):
    row = record()
    if protection == 'reviewed': row['review_status'] = 'reviewed'
    if protection == 'approved': row['outreach_approved'] = True
    seed_old(db, row, 'qualified' if protection == 'qualified' else 'rejected')
    if protection == 'outbox':
        db.db.execute("INSERT INTO outbox SELECT id,'staged',updated_at FROM companies")
    if protection == 'suppressed_email': db.suppress('email', row['emails'][0]['email'], 'opt_out')
    if protection == 'suppressed_domain': db.suppress('domain', row['website'], 'opt_out')
    assert db.refresh_version(row['website'], cutoff()) is None
    assert db.save_company(row, 'qualified', refresh_before=cutoff(), expected_version='2020-01-01T00:00:00+00:00') in ('duplicate', 'suppressed')
    assert db.db.execute('SELECT count(*) FROM company_versions').fetchone()[0] == 1


def test_refresh_race_does_not_overwrite_newer_record(db):
    row = seed_old(db)
    generation = db.refresh_version(row['website'], cutoff())
    db.db.execute('UPDATE companies SET updated_at=?', (datetime.now(timezone.utc).isoformat(),))
    assert db.save_company(row, 'qualified', refresh_before=cutoff(), expected_version=generation) == 'duplicate'


def test_contact_collision_does_not_reassign_or_modify_company(db):
    row = seed_old(db)
    other = record()
    other['website'] = 'https://otherbusiness.com'
    other['emails'][0]['email'] = 'owner@otherbusiness.com'
    assert db.save_company(other, 'rejected') == 'saved'
    row['emails'] = other['emails']
    assert db.save_company(row, 'qualified', refresh_before=cutoff(), expected_version='2020-01-01T00:00:00+00:00') == 'duplicate'
    assert db.db.execute('SELECT count(*) FROM company_versions').fetchone()[0] == 2


def test_runner_refresh_ignores_old_research_checkpoint_and_is_idempotent(db):
    original = seed_old(db)
    db.checkpoint('research:roofbusiness.com', {'poison': 'old checkpoint must not be reused'})
    config = {'industries':['roofing'], 'locations':['New Martinsville, WV'], 'refresh_rejected_before':cutoff()}
    provider = FakeZyte()
    outcome = run(config, db, provider, FakeHunter())
    assert outcome['refreshed'] == 1 and outcome['qualified'] == 1
    calls = provider.calls
    repeated = run(config, db, provider, FakeHunter())
    assert repeated['refreshed'] == 0 and repeated['researched'] == 0
    assert provider.calls == calls
    assert db.db.execute('SELECT count(*) FROM company_versions').fetchone()[0] == 2


def test_failed_refresh_keeps_prior_active_evidence(db):
    from lead_agent.providers import ProviderFailure
    original = seed_old(db)
    provider = FakeZyte()
    def fail(url): raise ProviderFailure('network_failure')
    provider.fetch = fail
    outcome = run({'industries':['roofing'], 'locations':['New Martinsville, WV'], 'refresh_rejected_before':cutoff()}, db, provider)
    assert outcome['errors'] == 1 and outcome['refreshed'] == 0
    assert json.loads(db.db.execute('SELECT payload FROM companies').fetchone()[0]) == original
    assert db.db.execute('SELECT count(*) FROM company_versions').fetchone()[0] == 1


@pytest.mark.parametrize('timestamp', ['2020-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00', 'bad', None])
def test_stale_missing_or_future_verification_is_not_eligible(timestamp):
    row = record()
    row['emails'][0]['provider_verified_at'] = timestamp
    assert strict_qualification(row)['qualification'] == 'rejected'


def test_qualification_requires_check_time_and_method():
    contact = record()['emails'][0]
    assert fresh_verification(contact)
    for field in ('checked_at', 'provider_verified_at', 'verification_method'):
        changed = dict(contact)
        changed.pop(field)
        assert not fresh_verification(changed)


def test_staging_rechecks_expiry_without_changing_saved_evidence(db):
    row = strict_qualification(record())
    assert row['qualification'] == 'qualified'
    row['emails'][0]['provider_verified_at'] = '2020-01-01'
    db.save_company(row, 'qualified')
    session = Mock()
    session.request.return_value.status_code = 200
    session.request.return_value.json.side_effect = [{'values':[LIVE_HEADERS]}, {'values':[HEADERS]}]
    outcome = stage(db, session, 'x' * 30)
    assert outcome['verification_or_qualification_blocked'] == 1
    assert outcome['staged'] == 0
    assert all(call.args[0] == 'GET' for call in session.request.call_args_list)
    assert json.loads(db.db.execute('SELECT payload FROM companies').fetchone()[0]) == row
    assert db.db.execute('SELECT status FROM outbox').fetchone()[0] == 'pending_review'


@pytest.mark.parametrize('value', ['2020-01-01', '2099-01-01T00:00:00+00:00', 'invalid'])
def test_invalid_refresh_config_rejected(tmp_path, value):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'industries':['roofing'], 'locations':['WV'], 'refresh_rejected_before':value}))
    with pytest.raises(ValueError): load_config(path)


def test_budget_interruption_resumes_completed_enrichment_and_verification(db):
    from lead_agent.store import BudgetExceeded
    class InterruptedHunter:
        def __init__(self):
            self.contact_calls = 0
            self.verify_calls = []
            self.interrupted = False
        def contacts(self, domain):
            self.contact_calls += 1
            return [{'email':'second@roofbusiness.com', 'name':'Second Owner', 'role':'Owner',
                     'source_urls':['https://roofbusiness.com/about'], 'verification_status':'unverified'}]
        def verify(self, email):
            self.verify_calls.append(email)
            if email.startswith('second') and not self.interrupted:
                self.interrupted = True
                raise BudgetExceeded('synthetic interruption')
            return FakeHunter().verify(email)
    config = {'industries':['roofing'], 'locations':['New Martinsville, WV']}
    provider, hunter = FakeZyte(), InterruptedHunter()
    first = run(config, db, provider, hunter)
    assert first['status'] == 'provider_budget_reached'
    assert db.metrics()['companies'] == 0
    fetches = provider.calls
    second = run(config, db, provider, hunter)
    assert second['qualified'] == 1
    assert provider.calls == fetches
    assert hunter.contact_calls == 1
    assert hunter.verify_calls.count('alex@roofbusiness.com') == 1
    assert hunter.verify_calls.count('second@roofbusiness.com') == 2


def test_expired_verification_checkpoint_is_not_reused(db):
    from test_persistent_agent import FakeHunter
    config = {'industries':['roofing'], 'locations':['New Martinsville, WV']}
    # Reproduce a previously interrupted run with a stale verifier checkpoint.
    db.checkpoint('research:roofbusiness.com:hunter_verify:alex@roofbusiness.com', {
        'verification_status':'independently_verified', 'verification_method':'fixture_only',
        'checked_at':'2020-01-01T00:00:00+00:00', 'provider_verified_at':'2020-01-01'})
    hunter = Mock(wraps=FakeHunter())
    assert run(config, db, FakeZyte(), hunter)['qualified'] == 1
    hunter.verify.assert_called_once_with('alex@roofbusiness.com')


def test_refresh_retains_historical_contact_deduplication(db):
    original = seed_old(db)
    updated = copy.deepcopy(original)
    updated['emails'] = []
    assert db.save_company(updated, 'rejected', refresh_before=cutoff(), expected_version='2020-01-01T00:00:00+00:00') == 'saved'
    another = record()
    another['website'] = 'https://anotherbusiness.com'
    assert db.save_company(another, 'qualified') == 'duplicate'
    assert db.metrics()['companies'] == 1
