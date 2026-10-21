# Development status and operating instructions

The persistent autonomous runner lives in lead_agent/. The local Scrapy engine
and extraction/discovery spiders live in aibuds_crawler/. The OAuth-protected
single-page Zyte extraction bridge lives in mcp/. These are separate entrypoints;
the URL-driven spider or MCP tool alone is not the autonomous lead generator.

No deployment, scheduling activation, credential changes, campaign-state writes,
or outreach execution are authorized by a source cleanup. Keep the existing
outreach repository, service, state and schedules untouched.

## Current implementation

- Bounded Zyte Search from configurable industries/locations; domain deduplication.
- Local robots-aware website research, at most three pages, page checkpoints.
- Facts, attributed operational signals, quoted hypotheses and explicit unknowns.
- Published Person JSON-LD linkage; optional Hunter enrichment and verification.
- Strict qualification: supported company identity, US country, service/workflow
  evidence, relevant named person and fresh independently verified nongeneric email.
- SQLite persistence, immutable research versions, suppressions, budget accounting,
  per-job retries/backoff, stage metrics, leases, and restart checkpoints.
- Explicit rejected-only refresh for stale unreviewed records. Reviewed/approved/
  queued records and suppressions cannot be overwritten. Historical contact
  identities remain reserved for deduplication. Existing evidence is retained.
- Read-only campaign snapshot suppression and version-bound local approval.
- Approved handoff preview does not send/import or write campaign state.
- Existing intake schema read-only sync and dedicated Google Sheets review tab.
  RAW cell writes, stable IDs and uncertain-write reconciliation are retained.
- Optional localhost Ollama hypotheses; no model-created contacts or score boosts.
- Optional direct scrapy-zyte-api transport, with bounded requests and SDK attempts.
- Separate Railway worker preparation with scheduling disabled by default.
- GitHub OAuth MCP contact-extraction tool, owner restriction and hourly budget.

## Commands

```bash
python -m pip install -r requirements-dev.txt -r requirements-direct.txt
python -m pytest tests -q
python -m unittest discover -s mcp -v
python tests/fixture_crawl.py
python tests/fixture_pipeline.py
python -m scrapy list
python -m lead_agent run --config config/targets.example.json
python -m lead_agent.validation
```

Both default run/validation commands are zero-call, zero-DB-write dry runs. Use
--execute only for explicitly authorized provider execution. The bounded live
harness also requires --allow-provider-charges. No key means no provider execution.
Credentials come from process environment; the CLI does not load another
project's .env. RUNBOOK.md includes Windows setup and private target config.

## Persistence and safety

Research versions preserve earlier payloads. Set optional refresh_rejected_before
to a timezone-aware past cutoff to refresh eligible stale rejected records that
reappear in bounded discovery. Records absent from search and already-reviewed
records need a future explicit re-review design. Successful page, enrichment,
and verification checkpoints survive interruption; expired verification is not
reused as proof. Independently verified contact checks expire after 30 days.

The read-only campaign snapshot must be current and authoritative; file mtime
cannot establish completeness. Approval binds to an exact payload version.
Suppression or verification expiry blocks preview/staging. Live campaign handoff
and sends are not implemented. Review Sheets writes still require authorization.

Use a persistent absolute /data database path only in a future approved Railway
worker deployment. ENABLE_SCHEDULE remains false. No existing Railway service
configuration was changed. Daily cost figures are estimates, not invoice caps.
Zyte and Hunter may charge; keep account-side spending controls.

## Validation evidence and remaining gates

Before this cleanup, the baseline had 88 pytest tests with 14 subtests, plus
11 MCP tests. A prior single live Zyte extraction of https://aibudbots.ai/ worked
through the existing bridge; it is not a multi-industry Search or mailbox test.
Reserved demo-email filtering was fixed in source after that observation.
The cleanup's final checks are recorded in CODEX_HANDOFF.md.

Python 3.12/Linux and Scrapy 2.19 are locally tested. Windows Python 3.13, live
Search schema/account compatibility, Hunter provenance/data quality, Ollama
inference, authoritative campaign export completeness, live Google review writes,
and scheduled-worker operation require controlled validation. No Apollo/LinkedIn/
Places or customer-review collection provider is claimed implemented. A passing
startup, unit test, HTTP response or high score does not prove lead quality.

The older independent OSM Phase 1 implementation remains preserved on
archive/session-phase1-discovery at 622e647. Do not reset that branch or blindly
merge its competing package into the current runner. No local research database
was migrated by this cleanup; tests use temporary synthetic state.

Rollback: revert only the cleanup commit if needed. Preserve SQLite state and
research/suppression history. Source rollback is not approval to alter a deployed
service. Back up SQLite using its backup API before a future approved migration.
