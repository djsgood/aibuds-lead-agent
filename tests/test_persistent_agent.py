import copy
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import pytest
from lead_agent.store import Store, BudgetExceeded
from lead_agent.providers import Hunter, Zyte, ProviderFailure
from lead_agent.runner import run, main
from lead_agent.research import research, strict_qualification
from lead_agent.handoff import stage, LIVE_HEADERS
from lead_agent.models import validate_hypotheses
from aibuds_crawler.sheets import HEADERS


@pytest.fixture
def db(tmp_path):
    s=Store(tmp_path/"agent.sqlite3")
    yield s
    s.close()


def record():
    return {"lead_id":"fixture","website":"https://roofbusiness.com/","business_name":"Roof Business",
            "business_name_source_url":"https://roofbusiness.com/about","country":"US","employee_count":10,
            "industry_match":True,"target_industry":"roofing","target_location":"New Martinsville, WV",
            "hypotheses":[{"summary":"Follow-up may help","evidence":{"source_url":"https://roofbusiness.com/","quote":"Request a quote"}}],
            "emails":[{"email":"alex@roofbusiness.com","name":"Alex Owner","role":"Owner","source_url":"https://roofbusiness.com/about","source_urls":["https://roofbusiness.com/about"],"verification_status":"independently_verified","verification_method":"fixture_only","checked_at":datetime.now(timezone.utc).isoformat(),"provider_verified_at":datetime.now(timezone.utc).date().isoformat()}],
            "phones":[],"source_urls":["https://roofbusiness.com/"],"fit_evidence":[{"source_url":"https://roofbusiness.com/","quote":"Request a quote"}],"discovery_source_url":"https://google.com/search?q=roofing","collected_at":"2026-10-21T00:00:00+00:00","email_status":"published_not_deliverability_verified","collection_errors":[]}


def test_persistence_survives_restart(tmp_path):
    path=tmp_path/"persist.sqlite3"
    s=Store(path); s.save_company(record()); s.checkpoint("cursor",{"page":2}); s.close()
    s=Store(path)
    assert s.known("https://www.roofbusiness.com/contact")
    assert s.get_checkpoint("cursor")=={"page":2}
    assert s.metrics()["contacts"]==1
    s.close()


def test_duplicate_domain_and_contact_are_not_overwritten(db):
    assert db.save_company(record())=="saved"
    changed=record(); changed["business_name"]="New Name"
    assert db.save_company(changed)=="duplicate"
    changed["website"]="https://anotherroof.com/"
    assert db.save_company(changed)=="duplicate"
    assert db.metrics()["companies"]==1


def test_suppression_wins_across_runs(db):
    db.suppress("email"," ALEX@roofbusiness.com ","opt_out")
    assert db.save_company(record(),"qualified")=="suppressed"
    assert db.metrics()["outbox"]=={}


def test_domain_suppression_covers_www(db):
    db.suppress("domain","roofbusiness.com","contacted")
    assert db.blocked("https://www.roofbusiness.com/")


def test_atomic_budget_counts_failed_attempts(db):
    session=Mock(); session.request.return_value.status_code=500
    provider=Zyte("private-key",db,{"max_calls":1,"max_cost":1,"estimated_cost":0.1},session)
    with patch("lead_agent.providers.time.sleep"), pytest.raises(BudgetExceeded): provider.search("roofing")
    assert session.request.call_count==1
    assert db.metrics()["budgets"][0]["calls"]==1


def test_nonfinite_budget_rejected(db):
    with pytest.raises(ValueError): db.reserve_call("x",1,float("nan"),0.1)


def test_concurrent_worker_claim(db):
    assert db.claim("worker","one")
    assert not db.claim("worker","two")
    db.release("worker","two")
    assert not db.claim("worker","two")
    db.release("worker","one")
    assert db.claim("worker","two")


@pytest.mark.parametrize("verification",["unverified","accept_all","unknown","webmail","invalid","pending","unavailable"])
def test_unverified_and_risky_email_cannot_qualify(verification):
    row=record(); row["emails"][0]["verification_status"]=verification
    assert strict_qualification(row)["qualification"]=="rejected"


def test_generic_inbox_cannot_qualify_even_with_named_person():
    row=record(); row["emails"][0]["email"]="info@roofbusiness.com"
    assert strict_qualification(row)["qualification"]=="rejected"


def test_unknown_headcount_not_invented_or_scored():
    row=record(); row["employee_count"]=None
    result=strict_qualification(row)
    assert result["qualification"]=="qualified"
    assert result["employee_count"] is None
    assert result["score_components"]["commercial_size_preference"]==0


def test_weak_need_and_unknown_country_rejected():
    row=record(); row["hypotheses"]=[]; row["country"]=None
    result=strict_qualification(row)
    assert "no_evidenced_automation_workflow" in result["rejection_reasons"]
    assert "us_location_unconfirmed" in result["rejection_reasons"]


def test_hunter_accept_all_and_stale_result_not_verified(db):
    session=Mock(); session.request.return_value.status_code=200
    session.request.return_value.content=b'{}'
    data={"email":"alex@roofbusiness.com","status":"valid","smtp_check":True,"accept_all":True,"verification":{"date":datetime.now(timezone.utc).date().isoformat()}}
    session.request.return_value.json.return_value={"data":data}
    provider=Hunter("secret-key",db,{"max_calls":10,"max_cost":1,"estimated_cost":0.01},session)
    assert provider.verify(data["email"])["verification_status"]!="independently_verified"
    data["accept_all"]=False; data["verification"]["date"]="2020-01-01"
    assert provider.verify(data["email"])["verification_status"]!="independently_verified"
    assert session.request.call_args.kwargs["headers"]=={"X-API-KEY":"secret-key"}
    assert "api_key" not in session.request.call_args.kwargs["params"]


def test_dry_run_has_no_network_or_database(tmp_path,monkeypatch,capsys):
    path=tmp_path/"config.json"; path.write_text(json.dumps({"industries":["roofing","plumbing"],"locations":["New Martinsville, WV"]}))
    database=tmp_path/"never.sqlite3"; monkeypatch.setenv("LEAD_AGENT_DB",str(database))
    with patch("requests.Session.request",side_effect=AssertionError("network")):
        assert main(["run","--config",str(path),"--dry-run"])==0
    assert not database.exists()
    assert json.loads(capsys.readouterr().out)["network_calls"]==0


def test_model_cannot_create_factual_contact_fields():
    with pytest.raises(ValueError): validate_hypotheses([{"name":"Invented","email":"guessed@roofbusiness.com"}],[])


def test_existing_sheet_suppression_blocks_staging(db):
    row=strict_qualification(record()); db.save_company(row,"qualified")
    session=Mock(); session.request.return_value.status_code=200
    live=["","Roof Business","roofing","WV","https://roofbusiness.com","Alex","Owner","alex@roofbusiness.com","","","","Qualified","Sent 2026-10-03",""]
    session.request.return_value.json.side_effect=[{"values":[LIVE_HEADERS,live]},{"values":[HEADERS]}]
    result=stage(db,session,"x"*30)
    assert result["suppressed"]==1
    assert all(call.args[0]=="GET" for call in session.request.call_args_list)


def test_live_intake_never_write_target(db):
    session=Mock()
    with pytest.raises(ValueError): stage(db,session,"x"*30,"Qualified Prospects")
    session.request.assert_not_called()


class FakeZyte:
    def __init__(self): self.calls=0
    def search(self,query):
        self.calls+=1
        return {"source_url":"https://google.com/search?q=roofing","retrieved_at":"2026-10-21T00:00:00Z","results":[{"url":"https://roofbusiness.com/","title":"Roof Business"}]}
    def fetch(self,url):
        self.calls+=1
        if url.endswith("robots.txt"): return "User-agent: *\nAllow: /"
        return '''<body>Roofing New Martinsville. Request a quote. <a href="mailto:alex@roofbusiness.com">Email</a>
        <script type="application/ld+json">{"@type":"RoofingContractor","name":"Roof Business","address":{"addressCountry":"US"},"numberOfEmployees":12}</script>
        <script type="application/ld+json">{"@type":"Person","name":"Alex Owner","jobTitle":"Owner","email":"alex@roofbusiness.com"}</script></body>'''


class FakeHunter:
    def contacts(self,domain): return []
    def verify(self,email): return {"verification_status":"independently_verified","verification_method":"fixture_only","checked_at":datetime.now(timezone.utc).isoformat(),"provider_verified_at":datetime.now(timezone.utc).date().isoformat()}


def test_full_persistent_run_restart_and_staging(db):
    provider=FakeZyte()
    config={"industries":["roofing"],"locations":["New Martinsville, WV"],"max_businesses":2}
    result=run(config,db,provider,FakeHunter())
    assert result["qualified"]==1 and result["outreach_enabled"] is False
    calls=provider.calls
    result=run(config,db,provider,FakeHunter())
    assert result["researched"]==0 and provider.calls==calls
    session=Mock(); session.request.return_value.status_code=200
    session.request.return_value.json.side_effect=[{"values":[LIVE_HEADERS]},{"values":[HEADERS]},{"updates":{"updatedRows":1}}]
    assert stage(db,session,"x"*30)["staged"]==1
    assert session.request.call_args.args[0]=="POST"
    assert session.request.call_args.kwargs["params"]["valueInputOption"]=="RAW"


def test_missing_verifier_rejects_instead_of_qualifying(db):
    result=run({"industries":["roofing"],"locations":["New Martinsville, WV"]},db,FakeZyte())
    assert result["qualified"]==0 and result["rejected"]==1
    assert db.metrics()["outbox"]=={}
