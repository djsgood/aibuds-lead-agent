"""Review-only Sheets output, stable IDs, RAW cells, no campaign writes."""
import json
import os
import re
from urllib.parse import quote

HEADERS = ["Lead ID", "Business Name", "Website", "Contact Name", "Emails", "Phones",
           "Industry", "Location", "Fit Score", "Qualification", "Fit Reason", "Evidence",
           "Source URLs", "Discovery Source", "Collected At", "Email Status", "Review Status",
           "Outreach Approved", "Collection Errors"]


def cells(lead):
    return [lead["lead_id"], lead.get("business_name") or "", lead["website"], lead.get("contact_name") or "",
            json.dumps(lead["emails"], ensure_ascii=False), json.dumps(lead["phones"], ensure_ascii=False),
            lead["target_industry"], lead["target_location"], lead["fit_score"], lead["qualification"],
            lead["fit_reason"], json.dumps(lead["fit_evidence"], ensure_ascii=False),
            json.dumps(lead["source_urls"]), lead["discovery_source_url"], lead["collected_at"],
            lead["email_status"], "pending_review", False, json.dumps(lead["collection_errors"])]


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
        # Destination tab must already exist. Never overwrite a different schema.
        rows = self.request("GET", self.tab + "!A:S").get("values", [])
        if rows and rows[0] != HEADERS:
            raise ValueError("Review tab headers do not match; use a dedicated blank tab named Lead Review.")
        if not rows:
            self.request("PUT", self.tab + "!A1:S1", params={"valueInputOption": "RAW"}, json={"values": [HEADERS]})
        self.seen = {row[0] for row in rows[1:] if row}

    def save(self, lead):
        if lead["lead_id"] in self.seen:
            return "duplicate_skipped"
        # No blind retries: an append timeout may mean the row already exists.
        self.request("POST", self.tab + "!A:S", suffix=":append",
                     params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                     json={"values": [cells(lead)]})
        self.seen.add(lead["lead_id"])
        return "saved_for_review"


class ReviewSheetsPipeline:
    def open_spider(self, spider):
        self.sink = None
        if not spider.write_to_sheets:
            return
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        settings = spider.crawler.settings
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or settings.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet = os.environ.get("LEADS_SPREADSHEET_ID") or settings.get("LEADS_SPREADSHEET_ID")
        tab = os.environ.get("LEADS_SHEET_TAB") or settings.get("LEADS_SHEET_TAB", "Lead Review")
        if not raw or not sheet:
            raise ValueError("Configure GOOGLE_SERVICE_ACCOUNT_JSON and LEADS_SPREADSHEET_ID privately; or explicitly use write_to_sheets=false for a collection-only test.")
        try:
            info = json.loads(raw)
            credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except (ValueError, TypeError, KeyError):
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not a valid service account credential.") from None
        self.sink = ReviewSheet(sheet, tab, AuthorizedSession(credentials))
        self.sink.prepare()  # Fail before paid discovery if destination is unusable.

    def process_item(self, item, spider):
        if item.get("qualification") != "qualified":
            item["sheet_status"] = "not_qualified_not_written"
            return item
        if self.sink is None:
            item["sheet_status"] = "disabled_for_test"
            return item
        try:
            item["sheet_status"] = self.sink.save(item)
        except Exception:
            # Retain the lead in the crawler output instead of losing it on a write failure.
            item["sheet_status"] = "write_failed_or_uncertain"
            spider.crawler.stats.inc_value("lead_pipeline/sheet_errors")
            spider.logger.error("Sheet write failed or is uncertain; lead retained in crawler output: %s. Do not retry append blindly.", item["lead_id"])
        return item

    def close_spider(self, spider):
        if self.sink:
            self.sink.session.close()
