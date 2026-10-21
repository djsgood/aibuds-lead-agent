import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from aibuds_crawler.discovery import parse_list, business_url, domain_key, lead_id
from lead_agent.store import Store, BudgetExceeded, now
from lead_agent.providers import Zyte, Hunter, ProviderFailure
from lead_agent.research import research, strict_qualification, relevant_role


def load_config(path):
    config=json.loads(Path(path).read_text())
    config["industries"]=parse_list(config.get("industries"),"industries")
    config["locations"]=parse_list(config.get("locations"),"locations")
    if len(config["industries"])*len(config["locations"])>6: raise ValueError("Maximum six target combinations per run")
    if not 1 <= config.get("max_businesses",10) <= 50: raise ValueError("max_businesses must be 1–50")
    if not 1 <= config.get("pages_per_site",3) <= 3: raise ValueError("pages_per_site must be 1–3")
    if not 1 <= config.get("max_contacts_per_business",2) <= 3: raise ValueError("max_contacts_per_business must be 1–3")
    if not 30 <= config.get("max_runtime_seconds",600) <= 600: raise ValueError("Runtime must be 30–600 seconds")
    return config


def candidates(config, zyte, store):
    day=datetime.now(timezone.utc).date().isoformat()
    seen=set()
    for industry in config["industries"]:
        for location in config["locations"]:
            query=f'{industry} businesses {location} official website contact -site:yelp.com -site:angi.com -site:facebook.com'
            key="search:"+day+":"+query
            result=store.get_checkpoint(key)
            if result is None:
                result=zyte.search(query)
                store.checkpoint(key,result)
            for match in result["results"]:
                try: website=business_url(match.get("url",""))
                except (ValueError,TypeError): continue
                if not website: continue
                domain=domain_key(website)
                if domain in seen or store.known(website) or store.blocked(website): continue
                seen.add(domain)
                yield {"lead_id":lead_id(website),"website":website,"target_industry":industry,"target_location":location,
                       "discovery_title":match.get("title", ""),"discovery_source_url":result["source_url"],
                       "discovered_at":result["retrieved_at"],"discovery_query":query}


def run(config, store, zyte, hunter=None):
    run_id=store.start_run(config)
    if not store.claim("worker",run_id):
        store.end_run(run_id,"another_run_active")
        return {"status":"another_run_active"}
    counts={"researched":0,"qualified":0,"rejected":0,"suppressed":0,"errors":0}
    started=time.monotonic()
    status="completed"
    try:
        for candidate in candidates(config,zyte,store):
            if counts["researched"] >= config.get("max_businesses",10): break
            if time.monotonic()-started >= config.get("max_runtime_seconds",600):
                status="runtime_budget_reached"; break
            domain=domain_key(candidate["website"])
            try:
                record=store.get_checkpoint("research:"+domain)
                if record is None:
                    record=research(candidate,zyte,config.get("pages_per_site",3))
                    store.checkpoint("research:"+domain,record)
                if hunter:
                    try:
                        extra=hunter.contacts(domain)
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
                    relevant=[c for c in record["emails"] if c.get("name") and relevant_role(c.get("role"))]
                    for contact in relevant[:config.get("max_contacts_per_business",2)]:
                        if store.blocked(candidate["website"],[contact["email"]]): continue
                        try: contact.update(hunter.verify(contact["email"]))
                        except ProviderFailure as exc:
                            contact["verification_status"]="unavailable"
                            if "suppression_requested" in str(exc): store.suppress("email",contact["email"],"provider_suppression")
                record=strict_qualification(record,config.get("employee_min",5),config.get("employee_max",50))
                if store.blocked(record["website"],[e["email"] for e in record["emails"]]):
                    counts["suppressed"]+=1; continue
                outcome=store.save_company(record,record["qualification"])
                counts["researched"]+=1
                if outcome=="saved": counts[record["qualification"]]+=1
            except ProviderFailure as exc:
                counts["errors"]+=1
                store.checkpoint("error:"+domain,{"error":str(exc),"at":now()})
            # Candidate checkpoint is durable even if a subsequent provider or process fails.
            store.checkpoint("run:"+run_id,counts)
            if counts["researched"] >= config.get("max_businesses",10): break
    except BudgetExceeded:
        status="provider_budget_reached"
    except ProviderFailure:
        counts["errors"]+=1; status="discovery_failed"
    except BaseException:
        status="failed"
        raise
    finally:
        if status=="completed" and counts["errors"]: status="completed_with_errors"
        elif status=="completed" and counts["researched"]==0: status="no_new_prospects"
        store.end_run(run_id,status)
        store.release("worker",run_id)
    return {"run_id":run_id,"status":status,**counts,"outreach_enabled":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description="AIBudBots autonomous prospect research — no outreach")
    sub=parser.add_subparsers(dest="command",required=True)
    collect=sub.add_parser("run"); collect.add_argument("--config",required=True); collect.add_argument("--dry-run",action="store_true")
    metrics=sub.add_parser("metrics")
    sub.add_parser("stage",help="Sync existing prospects read-only, then write only qualified leads to the review tab")
    suppress=sub.add_parser("suppress"); suppress.add_argument("--kind",choices=["email","domain"],required=True); suppress.add_argument("--value",required=True); suppress.add_argument("--reason",required=True)
    args=parser.parse_args(argv)
    config=load_config(args.config) if args.command=="run" else {}
    if args.command=="run" and args.dry_run:
        print(json.dumps({"dry_run":True,"targets":config,"network_calls":0,"database_writes":0,"outreach_enabled":False},indent=2)); return 0
    store=Store(os.environ.get("LEAD_AGENT_DB","data/lead-agent.sqlite3"))
    try:
        if args.command=="metrics": result=store.metrics()
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
            zyte=Zyte(os.environ.get("ZYTE_API_KEY"),store,budget,search_domain=os.environ.get("ZYTE_SEARCH_DOMAIN","google.com"))
            hunter=Hunter(os.environ["HUNTER_API_KEY"],store,{"max_calls":int(os.environ.get("HUNTER_MAX_DAILY_CALLS","10")),"max_cost":float(os.environ.get("HUNTER_MAX_DAILY_COST_USD","1")),"estimated_cost":float(os.environ.get("HUNTER_ESTIMATED_CALL_COST_USD","0.05"))}) if os.environ.get("HUNTER_API_KEY") else None
            result=run(config,store,zyte,hunter)
        print(json.dumps(result,indent=2))
        return 0 if result.get("status") not in ("failed","discovery_failed","completed_with_errors") and not result.get("uncertain") else 1
    finally: store.close()
