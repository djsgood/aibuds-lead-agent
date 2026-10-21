import json
import sqlite3
import time
import uuid
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from aibuds_crawler.discovery import domain_key, lead_id


def now():
    return datetime.now(timezone.utc).isoformat()


def email_key(value):
    return str(value or "").strip().lower()


class BudgetExceeded(RuntimeError): pass


class Store:
    def __init__(self, path):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, domain TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, stage TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contacts(email TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), payload TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), source_url TEXT NOT NULL, quote TEXT NOT NULL, kind TEXT NOT NULL, retrieved_at TEXT NOT NULL, UNIQUE(company_id,source_url,quote,kind));
        CREATE TABLE IF NOT EXISTS suppressions(kind TEXT NOT NULL, value TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS budgets(day TEXT NOT NULL, provider TEXT NOT NULL, calls INTEGER NOT NULL, estimated_cost REAL NOT NULL, PRIMARY KEY(day,provider));
        CREATE TABLE IF NOT EXISTS checkpoints(source_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(company_id TEXT PRIMARY KEY REFERENCES companies(id), status TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(job_key TEXT PRIMARY KEY, stage TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, error_type TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS company_versions(company_id TEXT NOT NULL REFERENCES companies(id), version INTEGER NOT NULL, payload TEXT NOT NULL, stage TEXT NOT NULL, saved_at TEXT NOT NULL, PRIMARY KEY(company_id,version));
        CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def start_run(self, config):
        id_ = uuid.uuid4().hex
        self.db.execute("INSERT INTO runs VALUES (?,?,NULL,?,?)", (id_, now(), "running", json.dumps(config)))
        return id_

    def end_run(self, id_, status):
        self.db.execute("UPDATE runs SET ended_at=?, status=? WHERE id=?", (now(), status, id_))

    def claim(self, name, owner, seconds=900):
        with self.transaction():
            row = self.db.execute("SELECT * FROM leases WHERE name=?", (name,)).fetchone()
            if row and row["expires"] > time.time() and row["owner"] != owner:
                return False
            self.db.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?)", (name, owner, time.time()+seconds))
            return True

    def release(self, name, owner):
        self.db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    def reserve_call(self, provider, max_calls, max_cost, estimated_cost, day=None):
        if not all(math.isfinite(v) for v in (max_calls,max_cost,estimated_cost)) or estimated_cost < 0 or max_calls < 1 or max_cost < 0:
            raise ValueError("Invalid budget")
        day = day or datetime.now(timezone.utc).date().isoformat()
        with self.transaction():
            row = self.db.execute("SELECT calls,estimated_cost FROM budgets WHERE day=? AND provider=?", (day, provider)).fetchone()
            calls, cost = (row["calls"], row["estimated_cost"]) if row else (0, 0)
            if calls + 1 > max_calls or cost + estimated_cost > max_cost + 1e-9:
                raise BudgetExceeded("Daily provider budget exhausted")
            self.db.execute("INSERT OR REPLACE INTO budgets VALUES (?,?,?,?)", (day, provider, calls+1, cost+estimated_cost))

    def suppress(self, kind, value, reason):
        if kind not in ("email", "domain"):
            raise ValueError("Suppression kind must be email or domain")
        value = email_key(value) if kind == "email" else domain_key("https://" + value.removeprefix("https://").split("/")[0])
        if not value:
            raise ValueError("Empty suppression")
        self.db.execute("INSERT OR IGNORE INTO suppressions VALUES (?,?,?,?)", (kind,value,reason,now()))

    def blocked(self, website, emails=()):
        domain = domain_key(website)
        if self.db.execute("SELECT 1 FROM suppressions WHERE kind='domain' AND value=?", (domain,)).fetchone():
            return True
        return any(self.db.execute("SELECT 1 FROM suppressions WHERE kind='email' AND value=?", (email_key(e),)).fetchone() for e in emails)

    def known(self, website, emails=()):
        if self.db.execute("SELECT 1 FROM companies WHERE domain=?", (domain_key(website),)).fetchone():
            return True
        return any(self.db.execute("SELECT 1 FROM contacts WHERE email=?", (email_key(e),)).fetchone() for e in emails)

    def refresh_version(self, website, before):
        """Return the prior generation eligible for explicit rejected-only refresh."""
        if not before or self.blocked(website):
            return None
        row = self.db.execute("SELECT * FROM companies WHERE domain=?", (domain_key(website),)).fetchone()
        if not row or row["stage"] != "rejected":
            return None
        if self.db.execute("SELECT 1 FROM outbox WHERE company_id=?", (row["id"],)).fetchone():
            return None
        record = json.loads(row["payload"])
        if record.get("outreach_approved") or record.get("review_status") not in (None, "pending_review"):
            return None
        if self.blocked(website, [e["email"] for e in record.get("emails", [])]):
            return None
        cutoff = datetime.fromisoformat(before)
        if cutoff.tzinfo is None or cutoff > datetime.now(timezone.utc):
            raise ValueError("Refresh cutoff must be timezone-aware and not in the future")
        if datetime.fromisoformat(row["updated_at"]) >= cutoff:
            return None
        return row["updated_at"]

    def save_company(self, record, stage="researched", *, refresh_before=None, expected_version=None):
        id_, domain = lead_id(record["website"]), domain_key(record["website"])
        emails = [e["email"] for e in record.get("emails", [])]
        if self.blocked(record["website"], emails):
            return "suppressed"
        with self.transaction():
            if self.blocked(record["website"], emails):
                return "suppressed"
            if any(self.db.execute("SELECT 1 FROM contacts WHERE email=? AND company_id!=?", (email_key(e),id_)).fetchone() for e in emails):
                return "duplicate"
            prior = self.db.execute("SELECT * FROM companies WHERE id=?", (id_,)).fetchone()
            if prior:
                generation = self.refresh_version(record["website"], refresh_before)
                if not generation or generation != expected_version:
                    return "duplicate"
                # Snapshot the old active version before replacing it. Contacts/evidence
                # remain in its immutable payload; suppressions are never deleted.
                self.db.execute("INSERT OR IGNORE INTO company_versions VALUES (?,?,?,?,?)", (id_,1,prior["payload"],prior["stage"],prior["updated_at"]))
                record = {**record, "outreach_approved":False, "review_status":"pending_review"}
                self.db.execute("UPDATE companies SET payload=?,stage=?,updated_at=? WHERE id=?", (json.dumps(record),stage,now(),id_))
            else:
                self.db.execute("INSERT INTO companies VALUES (?,?,?,?,?)", (id_,domain,json.dumps(record),stage,now()))
            version = self.db.execute("SELECT COALESCE(MAX(version),0)+1 FROM company_versions WHERE company_id=?", (id_,)).fetchone()[0]
            self.db.execute("INSERT INTO company_versions VALUES (?,?,?,?,?)", (id_,version,json.dumps(record),stage,now()))
            for contact in record.get("emails", []):
                # A duplicate contact never gets reassigned to another company silently.
                self.db.execute("INSERT INTO contacts VALUES (?,?,?,?) ON CONFLICT(email) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at WHERE contacts.company_id=excluded.company_id", (email_key(contact["email"]),id_,json.dumps(contact),now()))
            for ev in record.get("fit_evidence", []):
                self.db.execute("INSERT OR IGNORE INTO evidence(company_id,source_url,quote,kind,retrieved_at) VALUES (?,?,?,?,?)", (id_,ev["source_url"],ev.get("quote", ""),ev.get("kind", "observed_fact"),now()))
            if stage == "qualified":
                self.db.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?)", (id_,"pending_review",now()))
        return "saved"

    def checkpoint(self, key, payload):
        self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)", (key,json.dumps(payload),now()))

    def get_checkpoint(self, key):
        row = self.db.execute("SELECT payload FROM checkpoints WHERE source_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def job_due(self, key):
        row=self.db.execute("SELECT * FROM jobs WHERE job_key=?",(key,)).fetchone()
        return not row or (row["stage"] != "failed_enrichment" and row["retry_at"] <= time.time())

    def job_stage(self, key, stage):
        self.db.execute("INSERT INTO jobs(job_key,stage,updated_at) VALUES (?,?,?) ON CONFLICT(job_key) DO UPDATE SET stage=excluded.stage,retry_at=0,updated_at=excluded.updated_at",(key,stage,now()))

    def job_failed(self, key, error_type):
        with self.transaction():
            row=self.db.execute("SELECT failures FROM jobs WHERE job_key=?",(key,)).fetchone()
            failures=(row[0] if row else 0)+1
            stage="failed_enrichment" if failures >= 3 else "retry_pending"
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)",(key,stage,failures,time.time()+min(3600,60*2**(failures-1)),error_type,now()))

    def metrics(self):
        return {"job_stages":dict(self.db.execute("SELECT stage,count(*) FROM jobs GROUP BY stage")),
                "job_failures":dict(self.db.execute("SELECT error_type,count(*) FROM jobs WHERE error_type IS NOT NULL GROUP BY error_type")),
                "companies": self.db.execute("SELECT count(*) FROM companies").fetchone()[0],
                "research_versions": self.db.execute("SELECT count(*) FROM company_versions").fetchone()[0],
                "contacts": self.db.execute("SELECT count(*) FROM contacts").fetchone()[0],
                "suppressions": self.db.execute("SELECT count(*) FROM suppressions").fetchone()[0],
                "stages": dict(self.db.execute("SELECT stage,count(*) FROM companies GROUP BY stage")),
                "budgets": [dict(r) for r in self.db.execute("SELECT * FROM budgets ORDER BY day DESC LIMIT 20")],
                "outbox": dict(self.db.execute("SELECT status,count(*) FROM outbox GROUP BY status"))}

    def close(self): self.db.close()
