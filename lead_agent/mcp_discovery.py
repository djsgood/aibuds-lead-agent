"""Bounded on-demand discovery; no campaign, Sheets, or sending integration."""
import os
import json
from pathlib import Path

from lead_agent.providers import Zyte
from lead_agent.runner import run, validate_config
from lead_agent.store import Store, BudgetExceeded


class RunBudget:
    """Count every upstream attempt, including retries, against both limits."""
    def __init__(self, store, limit):
        self.store, self.limit, self.calls = store, limit, 0

    def reserve_call(self, *args, **kwargs):
        if self.calls >= self.limit:
            raise BudgetExceeded("Per-run request limit reached")
        self.store.reserve_call(*args, **kwargs)
        self.calls += 1


def discover(industries, locations, max_businesses=1, max_api_requests=3, dry_run=True):
    if type(max_businesses) is not int or not 1 <= max_businesses <= 5:
        raise ValueError("max_businesses must be 1–5")
    if type(max_api_requests) is not int or not 1 <= max_api_requests <= 10:
        raise ValueError("max_api_requests must be 1–10")
    config = validate_config({"industries": industries, "locations": locations,
                              "max_businesses": max_businesses, "pages_per_site": 1,
                              "max_runtime_seconds": 120})
    if dry_run:
        return {"dry_run": True, "targets": config, "max_api_requests": max_api_requests,
                "network_calls": 0, "database_writes": 0, "outreach_enabled": False}
    key = os.environ.get("ZYTE_API_KEY")
    if not key:
        raise ValueError("ZYTE_API_KEY is required for live discovery")
    path = os.environ.get("LEAD_AGENT_DB", str(Path.home() / "lead-agent.sqlite3"))
    store = Store(path)
    try:
        budget = RunBudget(store, max_api_requests)
        provider = Zyte(key, budget, {
            "max_calls": int(os.environ.get("LEAD_AGENT_MAX_DAILY_CALLS", "30")),
            "max_cost": float(os.environ.get("LEAD_AGENT_MAX_DAILY_COST_USD", "1")),
            "estimated_cost": float(os.environ.get("LEAD_AGENT_ESTIMATED_CALL_COST_USD", "0.01")),
        })
        existing = {row["id"] for row in store.db.execute("SELECT id FROM companies")}
        try:
            result = run(config, store, provider)
        finally:
            provider.session.close()
        prospects = []
        for row in store.db.execute("SELECT id, payload, stage FROM companies ORDER BY updated_at DESC"):
            if row["id"] in existing:
                continue
            record = json.loads(row["payload"])
            prospects.append({"lead_id": row["id"], "website": record.get("website"),
                              "business_name": record.get("business_name"), "stage": row["stage"],
                              "facts": record.get("facts", []), "emails": record.get("emails", []),
                              "discovery_source_url": record.get("discovery_source_url"),
                              "source_urls": record.get("source_urls", []),
                              "outreach_eligible": False})
        return {**result, "prospects": prospects[:max_businesses], "api_requests": budget.calls, "automatic_handoff": False,
                "verification": "No independent email verifier used; contacts cannot enter outreach.",
                "storage": "SQLite; mount persistent storage at LEAD_AGENT_DB for retention across deployments."}
    finally:
        store.close()
