import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from lead_agent.pipeline_utils import parse_list, business_url, domain_key, lead_id
from lead_agent.store import Store, BudgetExceeded, now
from lead_agent.providers import DiscoveryResearchProvider, Hunter, ProviderFailure
from lead_agent.research import research, strict_qualification, relevant_role, fresh_verification


def load_config(path):
    return validate_config(json.loads(Path(path).read_text()))


def validate_config(config):
    config=dict(config)
    config["industries"]=parse_list(config.get("industries"),"industries")
    config["locations"]=parse_list(config.get("locations"),"locations")
    if len(config["industries"])*len(config["locations"])>24: raise ValueError("Maximum 24 target combinations per run")
    if not 1 <= config.get("max_businesses",10) <= 50: raise ValueError("max_businesses must be 1–50")
    if not 1 <= config.get("pages_per_site",3) <= 3: raise ValueError("pages_per_site must be 1–3")
    if not 1 <= config.get("max_contacts_per_business",2) <= 3: raise ValueError("max_contacts_per_business must be 1–3")
    if not 30 <= config.get("max_runtime_seconds",600) <= 600: raise ValueError("Runtime must be 30–600 seconds")
    if config.get("refresh_rejected_before"):
        cutoff=datetime.fromisoformat(config["refresh_rejected_before"])
        if cutoff.tzinfo is None or cutoff > datetime.now(timezone.utc):
            raise ValueError("Refresh cutoff must be timezone-aware and not in the future")
    for field in ("exclude_domains", "exclude_emails", "decision_maker_roles"):
        values=config.get(field,[])
        if not isinstance(values,list) or not all(isinstance(v,str) and v.strip() for v in values):
            raise ValueError(field+" must be a list of nonempty strings")
    return config


def candidates(config, store, provider: DiscoveryResearchProvider):
    day=datetime.now(timezone.utc).date().isoformat()
    seen=set()
    excluded={domain_key("https://"+v.removeprefix("https://").removeprefix("http://")) for v in config.get("exclude_domains",[])}
    for industry in config["industries"]:
        for location in config["locations"]:
            query=f'{industry} businesses {location} official website contact -site:yelp.com -site:angi.com -site:facebook.com'
            key="search:"+day+":"+query
            result=store.get_checkpoint(key)
            if result is None:
                result=provider.search(query)
                store.checkpoint(key,result)
            for match in result["results"]:
                if not isinstance(match, dict): continue
                try: website=business_url(match.get("url",""))
                except (ValueError,TypeError): continue
                if not website: continue
                domain=domain_key(website)
                refresh_version=store.refresh_version(website,config.get("refresh_rejected_before"))
                if domain in excluded or domain in seen or store.blocked(website) or (store.known(website) and not refresh_version): continue
                seen.add(domain)
                yield {"lead_id":lead_id(website),"website":website,"target_industry":industry,"target_location":location,
                       "refresh_version":refresh_version,"discovery_title":match.get("title", ""),"discovery_source_url":result["source_url"],
                       "discovered_at":result["retrieved_at"],"discovery_query":query}


def run(config, store, provider: DiscoveryResearchProvider, hunter=None, interpreter=None):
    run_id=store.start_run(config)
    if not store.claim("worker",run_id):
        store.end_run(run_id,"another_run_active")
        return {"status":"another_run_active"}
    counts={"researched":0,"qualified":0,"rejected":0,"suppressed":0,"errors":0,"refreshed":0,"duplicates":0}
    started=time.monotonic()
    status="completed"
    failure_reason=None
    try:
        for candidate in candidates(config,store,provider):
            if counts["researched"] >= config.get("max_businesses",10): break
            if time.monotonic()-started >= config.get("max_runtime_seconds",600):
                status="runtime_budget_reached"; break
            domain=domain_key(candidate["website"])
            try:
                generation=candidate.get("refresh_version")
                research_key="research:"+domain+(":refresh:"+generation if generation else "")
                if not store.job_due(research_key): continue
                store.job_stage(research_key,"research_pending")
                record=store.get_checkpoint(research_key)
                if record is None:
                    record=research(candidate,provider,config.get("pages_per_site",3),checkpoint_store=store,checkpoint_prefix=research_key)
                    store.checkpoint(research_key,record)
                store.job_stage(research_key,"enrichment_pending")
                if hunter:
                    store.job_stage(research_key,"enriching")
                    try:
                        enrichment_key=research_key+":hunter_contacts"
                        extra=store.get_checkpoint(enrichment_key)
                        if extra is None:
                            extra=hunter.contacts(domain)
                            store.checkpoint(enrichment_key,extra)
                        for contact in extra:
                            existing=next((e for e in record["emails"] if e["email"]==contact["email"]),None)
                            if existing:
                                # Preserve published website evidence while adding provider-backed person/role.
                                existing["name"]=existing.get("name") or contact.get("name")
                                existing["role"]=existing.get("role") or contact.get("role")
                                existing["source_urls"]=list(dict.fromkeys((existing.get("source_urls") or [existing.get("source_url")])+contact["source_urls"]))
                            else: record["emails"].append(contact)
                    except ProviderFailure as exc:
                        record["collection_errors"].append({"provider":"hunter","error":str(exc)})
                        if "suppression_requested" in str(exc):
                            store.suppress("domain",domain,"provider_suppression"); counts["suppressed"]+=1; continue
                    store.job_stage(research_key,"verification_pending")
                    relevant=[c for c in record["emails"] if c.get("name") and relevant_role(c.get("role"))]
                    for contact in relevant[:config.get("max_contacts_per_business",2)]:
                        if store.blocked(candidate["website"],[contact["email"]]): continue
                        try:
                            verification_key=research_key+":hunter_verify:"+contact["email"]
                            verified=store.get_checkpoint(verification_key)
                            if verified is None or (verified.get("verification_status") != "invalid" and not fresh_verification(verified)):
                                verified=hunter.verify(contact["email"])
                                store.checkpoint(verification_key,verified)
                            contact.update(verified)
                        except ProviderFailure as exc:
                            contact["verification_status"]="unavailable"
                            if "suppression_requested" in str(exc): store.suppress("email",contact["email"],"provider_suppression")
                if interpreter:
                    # Interpretations remain separate from the deterministic qualification evidence.
                    try: record["model_hypotheses"]=interpreter.interpret(record.get("facts",[]))
                    except Exception: record["model_hypotheses"]=[]; record["model_status"]="unavailable_or_invalid"
                record["excluded_emails"]=[e.strip().lower() for e in config.get("exclude_emails",[])]
                record["decision_maker_roles"]=config.get("decision_maker_roles",[])
                record=strict_qualification(record,config.get("employee_min",5),config.get("employee_max",50))
                if store.blocked(record["website"],[e["email"] for e in record["emails"]]):
                    counts["suppressed"]+=1; continue
                outcome=store.save_company(record,record["qualification"],refresh_before=config.get("refresh_rejected_before"),expected_version=generation)
                store.job_stage(research_key,"ready_for_review" if record["qualification"]=="qualified" else "disqualified_prospect")
                counts["researched"]+=1
                if outcome=="saved":
                    counts[record["qualification"]]+=1
                    counts["refreshed"]+=int(bool(generation))
                elif outcome=="duplicate": counts["duplicates"]+=1
                elif outcome=="suppressed": counts["suppressed"]+=1
            except (ProviderFailure, ValueError, KeyError, TypeError) as exc:
                counts["errors"]+=1
                store.job_failed(research_key,type(exc).__name__)
                store.checkpoint("error:"+domain,{"error_type":type(exc).__name__,"at":now()})
            # Candidate checkpoint is durable even if a subsequent provider or process fails.
            store.checkpoint("run:"+run_id,counts)
            if counts["researched"] >= config.get("max_businesses",10): break
    except BudgetExceeded:
        status="provider_budget_reached"
    except ProviderFailure as exc:
        counts["errors"]+=1; status="discovery_failed"
        failure_reason=str(exc)
    except BaseException:
        status="failed"
        raise
    finally:
        if status=="completed" and counts["errors"]: status="completed_with_errors"
        elif status=="completed" and counts["researched"]==0: status="no_new_prospects"
        store.end_run(run_id,status)
        store.release("worker",run_id)
    return {"run_id":run_id,"status":status,**counts,"outreach_enabled":False, "failure_reason":failure_reason}


def main(argv=None):
    parser=argparse.ArgumentParser(description="AIBudBots autonomous prospect research — no outreach")
    sub=parser.add_subparsers(dest="command",required=True)
    collect=sub.add_parser("run"); collect.add_argument("--config",required=True); mode=collect.add_mutually_exclusive_group(); mode.add_argument("--dry-run",dest="dry_run",action="store_true",default=True); mode.add_argument("--execute",dest="dry_run",action="store_false",help="Explicitly allow budgeted provider calls and local persistence")
    metrics=sub.add_parser("metrics")
    sub.add_parser("stage",help="Sync existing prospects read-only, then write only qualified leads to the review tab")
    suppress=sub.add_parser("suppress"); suppress.add_argument("--kind",choices=["email","domain"],required=True); suppress.add_argument("--value",required=True); suppress.add_argument("--reason",required=True)
    sync=sub.add_parser("sync-campaign",help="Import suppressions from a read-only verified campaign snapshot")
    sync.add_argument("--snapshot",required=True)
    approval=sub.add_parser("approve",help="Explicit local version-specific review approval; no live handoff")
    approval.add_argument("--lead-id",required=True); approval.add_argument("--reviewer",required=True)
    sub.add_parser("handoff-preview",help="Validate approved queue without sending or writing campaign data")
    args=parser.parse_args(argv)
    config=load_config(args.config) if args.command=="run" else {}
    if args.command=="run" and args.dry_run:
        print(json.dumps({"dry_run":True,"targets":config,"network_calls":0,"database_writes":0,"outreach_enabled":False},indent=2)); return 0
    store=Store(os.environ.get("LEAD_AGENT_DB","data/lead-agent.sqlite3"))
    try:
        if args.command=="sync-campaign":
            from lead_agent.campaign_snapshot import sync_campaign_snapshot
            result=sync_campaign_snapshot(store,args.snapshot)
        elif args.command=="approve":
            from lead_agent.review import approve
            result=approve(store,args.lead_id,args.reviewer)
        elif args.command=="handoff-preview":
            from lead_agent.review import handoff_records
            preview=handoff_records(store)
            result={"eligible":len(preview["records"]),"blocked":preview["blocked"],"automatic_handoff":False,"dry_run":True}
        elif args.command=="metrics": result=store.metrics()
        elif args.command=="stage":
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            from lead_agent.handoff import stage
            info=json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
            creds=service_account.Credentials.from_service_account_info(info,scopes=["https://www.googleapis.com/auth/spreadsheets"])
            with AuthorizedSession(creds) as session:
                result=stage(store,session,os.environ.get("LEADS_SPREADSHEET_ID", ""),os.environ.get("LEADS_SHEET_TAB", "Lead Review"))
        elif args.command=="suppress":
            store.suppress(args.kind,args.value,args.reason); result={"status":"suppressed"}
        else:
            budget={"max_calls":int(os.environ.get("LEAD_AGENT_MAX_DAILY_CALLS","60")),"max_cost":float(os.environ.get("LEAD_AGENT_MAX_DAILY_COST_USD","1")),"estimated_cost":float(os.environ.get("LEAD_AGENT_ESTIMATED_CALL_COST_USD","0.01"))}
            hunter=Hunter(os.environ["HUNTER_API_KEY"],store,{"max_calls":int(os.environ.get("HUNTER_MAX_DAILY_CALLS","10")),"max_cost":float(os.environ.get("HUNTER_MAX_DAILY_COST_USD","1")),"estimated_cost":float(os.environ.get("HUNTER_ESTIMATED_CALL_COST_USD","0.05"))}) if os.environ.get("HUNTER_API_KEY") else None
            interpreter=None
            model_backend=os.environ.get("LEAD_AGENT_MODEL_BACKEND", "").strip().lower()
            if model_backend == "ollama":
                from lead_agent.models import OllamaInterpreter
                interpreter=OllamaInterpreter(os.environ.get("OLLAMA_MODEL", ""))
            elif model_backend == "openai":
                from lead_agent.models import OpenAIResponsesInterpreter
                interpreter=OpenAIResponsesInterpreter(os.environ.get("OPENAI_MODEL", "gpt-5-mini"))
            raise SystemExit("No discovery provider is configured; connect a replacement provider before using --execute.")
        print(json.dumps(result,indent=2))
        return 0 if result.get("status") not in ("failed","discovery_failed","completed_with_errors") and not result.get("uncertain") and not result.get("verification_or_qualification_blocked") else 1
    finally: store.close()
