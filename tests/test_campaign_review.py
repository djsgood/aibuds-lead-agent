import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import importlib
import sys

import pytest
from lead_agent.campaign_snapshot import sync_campaign_snapshot
from lead_agent.review import approve, handoff_records
from lead_agent.research import strict_qualification
from lead_agent.store import Store
from test_persistent_agent import record


@pytest.fixture
def db(tmp_path):
    store=Store(tmp_path/'test.sqlite3')
    yield store
    store.close()


def snapshot(tmp_path, rows=None):
    path=tmp_path/'snapshot.json'
    path.write_text(json.dumps(rows if rows is not None else [{
        'company_name':'Other Business', 'contact_email':'owner@otherbusiness.com',
        'website_url':'https://otherbusiness.com', 'opted_out_at':datetime.now(timezone.utc).isoformat()}]))
    return path


def test_read_only_snapshot_preserves_file_and_suppresses(db,tmp_path):
    path=snapshot(tmp_path)
    before=path.read_bytes()
    assert sync_campaign_snapshot(db,path)['campaign_modified'] is False
    assert path.read_bytes()==before
    assert db.blocked('https://otherbusiness.com')
    assert db.blocked('https://differentbusiness.com',['owner@otherbusiness.com'])
    sync_campaign_snapshot(db,path)
    assert db.metrics()['suppressions']==2


def test_bad_snapshot_import_is_atomic(db,tmp_path):
    path=snapshot(tmp_path,[{'company_name':'Other','contact_email':'owner@otherbusiness.com'}, {'contact_email':'invalid'}])
    with pytest.raises(ValueError): sync_campaign_snapshot(db,path)
    assert db.metrics()['suppressions']==0
    assert db.get_checkpoint('campaign_suppression_sync') is None


def test_stale_snapshot_fails_closed(db,tmp_path):
    path=snapshot(tmp_path)
    os.utime(path,(1,1))
    with pytest.raises(ValueError): sync_campaign_snapshot(db,path)


def test_review_requires_fresh_sync_and_explicit_approval(db,tmp_path):
    row=strict_qualification(record()); db.save_company(row,'qualified')
    ident=db.db.execute('SELECT id FROM companies').fetchone()[0]
    with pytest.raises(ValueError): approve(db,ident,'Owner')
    sync_campaign_snapshot(db,snapshot(tmp_path))
    assert handoff_records(db)['records']==[]
    approve(db,ident,'Owner')
    result=handoff_records(db)
    assert len(result['records'])==1 and result['automatic_handoff'] is False
    db.suppress('email',row['emails'][0]['email'],'new_opt_out')
    assert handoff_records(db)['records']==[]
    assert handoff_records(db)['blocked']==1


def test_payload_change_invalidates_approval(db,tmp_path):
    row=strict_qualification(record()); db.save_company(row,'qualified')
    sync_campaign_snapshot(db,snapshot(tmp_path))
    ident=db.db.execute('SELECT id FROM companies').fetchone()[0]
    approve(db,ident,'Owner')
    row['business_name']='Changed'
    db.db.execute('UPDATE companies SET payload=?',(json.dumps(row),))
    assert handoff_records(db)['blocked']==1


def test_direct_settings_classes_exist_and_limits(monkeypatch):
    pytest.importorskip('scrapy_zyte_api')
    from scrapy.utils.misc import load_object
    monkeypatch.setenv('ZYTE_API_KEY','dummy-no-live-calls')
    sys.modules.pop('lead_agent.direct_settings',None)
    settings=importlib.import_module('lead_agent.direct_settings')
    for target in settings.DOWNLOAD_HANDLERS.values(): assert load_object(target)
    for target in settings.DOWNLOADER_MIDDLEWARES: assert load_object(target)
    assert load_object(settings.REQUEST_FINGERPRINTER_CLASS)
    assert settings.ZYTE_API_MAX_REQUESTS==4
    assert settings.RETRY_TIMES==0
    assert settings.ROBOTSTXT_OBEY is True
    assert settings.REDIRECT_ENABLED is False


def test_expired_sync_blocks_previously_approved_queue(db,tmp_path):
    row=strict_qualification(record()); db.save_company(row,'qualified')
    sync_campaign_snapshot(db,snapshot(tmp_path))
    ident=db.db.execute('SELECT id FROM companies').fetchone()[0]
    approve(db,ident,'Owner')
    sync=db.get_checkpoint('campaign_suppression_sync'); sync['at']='2020-01-01T00:00:00+00:00'
    db.checkpoint('campaign_suppression_sync',sync)
    with pytest.raises(ValueError): handoff_records(db)


def test_run_cli_defaults_to_zero_call_dry_run(tmp_path, monkeypatch, capsys):
    from lead_agent.runner import main
    path=tmp_path/'targets.json'
    path.write_text(json.dumps({'industries':['roofing'],'locations':['WV']}))
    database=tmp_path/'never.sqlite3'; monkeypatch.setenv('LEAD_AGENT_DB',str(database))
    assert main(['run','--config',str(path)])==0
    assert json.loads(capsys.readouterr().out)['network_calls']==0
    assert not database.exists()


def test_excluded_domain_never_researched(db):
    from lead_agent.runner import run
    from test_persistent_agent import FakeZyte
    provider=FakeZyte()
    result=run({'industries':['roofing'],'locations':['WV'],'exclude_domains':['roofbusiness.com']},db,provider)
    assert result['researched']==0
    assert provider.calls==1  # discovery only


def test_role_and_email_exclusions_block_qualification():
    row=record(); row['excluded_emails']=['alex@roofbusiness.com']
    assert strict_qualification(row)['qualification']=='rejected'
    row=record(); row['decision_maker_roles']=['office manager']
    assert strict_qualification(row)['qualification']=='rejected'


def test_page_checkpoint_survives_interrupted_research(db):
    from lead_agent.research import research
    from test_persistent_agent import FakeZyte
    from aibuds_crawler.discovery import lead_id
    candidate={'website':'https://roofbusiness.com/','lead_id':lead_id('https://roofbusiness.com'),
               'target_industry':'roofing','target_location':'WV'}
    provider=FakeZyte()
    research(candidate,provider,1,checkpoint_store=db,checkpoint_prefix='fixture')
    calls=provider.calls
    research(candidate,provider,1,checkpoint_store=db,checkpoint_prefix='fixture')
    assert provider.calls==calls+1  # robots policy is rechecked, page is resumed


def test_persistent_job_retry_is_bounded_and_backed_off(db):
    db.job_stage('company:generation1','enriching')
    for index in range(3):
        db.job_failed('company:generation1','ProviderFailure')
        assert db.job_due('company:generation1') is False
    assert db.metrics()['job_stages']['failed_enrichment']==1
    assert db.job_due('company:generation2') is True
