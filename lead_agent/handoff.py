"""Review outbox only. No production campaign writes exist in this module."""
import json
from urllib.parse import quote
from aibuds_crawler.sheets import ReviewSheet
from lead_agent.store import now
from lead_agent.research import strict_qualification

LIVE_HEADERS=["Date Added","Company","Industry","Location","Website","Decision Maker","Role","Email","Phone","Why AIBudBots Fits","Evidence / Notes","Qualification Status","Outreach Status","Last Contact / Next Step"]


def sync_existing(store, session, spreadsheet_id):
    url=f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/"+quote("'Qualified Prospects'!A:N",safe="")
    response=session.request("GET",url,timeout=25)
    if response.status_code != 200: raise RuntimeError("Existing prospect read failed; staging blocked")
    rows=response.json().get("values",[])
    if not rows or rows[0] != LIVE_HEADERS: raise ValueError("Existing prospect schema changed; staging blocked")
    imported=0
    for row in rows[1:]:
        website=row[4] if len(row)>4 else ""
        email=row[7] if len(row)>7 else ""
        status=row[12].lower() if len(row)>12 else ""
        reason="previously_contacted_or_suppressed" if any(w in status for w in ("sent","contacted","reply","unsubscribe","opted","suppress","bounce")) and status != "not contacted" else "existing_pipeline_record"
        if website:
            try: store.suppress("domain",website,reason)
            except ValueError: raise ValueError("Existing record domain could not be normalized; review required") from None
        if email: store.suppress("email",email,reason)
        imported+=1
    store.checkpoint("existing_sheet_sync",{"at":now(),"rows":imported,"schema_verified":True})
    return imported


def stage(store, session, spreadsheet_id, tab="Lead Review"):
    if tab.strip().lower()=="qualified prospects": raise ValueError("Writing the live intake is disabled")
    sync_existing(store,session,spreadsheet_id)  # Read-only sync immediately before staging.
    sink=ReviewSheet(spreadsheet_id,tab,session)
    sink.prepare()
    counts={"staged":0,"suppressed":0,"uncertain":0,"verification_or_qualification_blocked":0}
    for row in store.db.execute("SELECT c.id,c.payload FROM companies c JOIN outbox o ON o.company_id=c.id WHERE o.status='pending_review'").fetchall():
        record=json.loads(row["payload"])
        if store.blocked(record["website"],[e["email"] for e in record.get("emails",[])]):
            store.db.execute("UPDATE outbox SET status='suppressed',updated_at=? WHERE company_id=?",(now(),row["id"]))
            counts["suppressed"]+=1; continue
        # Re-evaluate saved evidence and verification freshness immediately before
        # export. Do not overwrite the saved/reviewed payload when a gate expires.
        from copy import deepcopy
        validated = strict_qualification(deepcopy(record))
        if validated["qualification"] != "qualified":
            counts["verification_or_qualification_blocked"]+=1
            continue
        try:
            status=sink.save(validated)
        except Exception:
            # A subsequent run re-reads remote IDs before deciding whether to append.
            counts["uncertain"]+=1
            continue
        store.db.execute("UPDATE outbox SET status=?,updated_at=? WHERE company_id=?",("staged",now(),row["id"]))
        counts["staged"]+=1
    return counts
