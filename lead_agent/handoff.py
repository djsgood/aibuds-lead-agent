"""Review outbox only. No production campaign writes exist in this module."""
import json
import re
from urllib.parse import quote
from lead_agent.store import now
from lead_agent.research import strict_qualification

LIVE_HEADERS=["Date Added","Company","Industry","Location","Website","Decision Maker","Role","Email","Phone","Why AIBudBots Fits","Evidence / Notes","Qualification Status","Outreach Status","Last Contact / Next Step"]
REVIEW_HEADERS = ["Lead ID", "Business Name", "Website", "Contact Name", "Emails", "Phones", "Industry", "Location", "Fit Score", "Qualification", "Fit Reason", "Evidence", "Source URLs", "Discovery Source", "Collected At", "Email Status", "Review Status", "Outreach Approved", "Collection Errors"]


class ReviewSheet:
    def __init__(self, spreadsheet_id, tab, session):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", spreadsheet_id):
            raise ValueError("Set LEADS_SPREADSHEET_ID to the destination review spreadsheet ID.")
        if not tab or len(tab) > 100:
            raise ValueError("Invalid LEADS_SHEET_TAB.")
        self.session, self.tab = session, "'" + tab.replace("'", "''") + "'"
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/"
        self.seen = set()

    def request(self, method, range_, **kwargs):
        response = self.session.request(method, self.base + quote(range_, safe="") + kwargs.pop("suffix", ""), timeout=25, **kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"Google Sheets HTTP {response.status_code}; check spreadsheet sharing and tab configuration.")
        return response.json()

    def prepare(self):
        rows = self.request("GET", self.tab + "!A:S").get("values", [])
        if rows and rows[0] != REVIEW_HEADERS:
            raise ValueError("Review tab headers do not match; use a dedicated blank tab named Lead Review.")
        if not rows:
            self.request("PUT", self.tab + "!A1:S1", params={"valueInputOption": "RAW"}, json={"values": [REVIEW_HEADERS]})
        self.seen = {row[0] for row in rows[1:] if row}

    def save(self, lead):
        if lead["lead_id"] in self.seen:
            return "duplicate_skipped"
        values = [lead["lead_id"], lead.get("business_name") or "", lead["website"], lead.get("contact_name") or "",
                  json.dumps(lead["emails"], ensure_ascii=False), json.dumps(lead["phones"], ensure_ascii=False), lead["target_industry"],
                  lead["target_location"], lead["fit_score"], lead["qualification"], lead["fit_reason"], json.dumps(lead["fit_evidence"], ensure_ascii=False),
                  json.dumps(lead["source_urls"]), lead["discovery_source_url"], lead["collected_at"], lead["email_status"], "pending_review", False,
                  json.dumps(lead["collection_errors"])]
        self.request("POST", self.tab + "!A:S", suffix=":append", params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": [values]})
        self.seen.add(lead["lead_id"])
        return "saved_for_review"


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
