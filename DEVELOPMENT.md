# Development status and operating instructions

Work is on `dev/autonomous-lead-pipeline`, not the branches connected to live
Railway services. Nothing here is production-approved or deployed.

## Current implementation

- Industries/locations -> bounded Zyte structured search -> deduplicated sites.
- robots.txt-aware research of up to three pages, with extraction reused from the
  tested crawler. Redirect-only sites currently defer for research rather than
  weakening target validation. Non-HTML/JavaScript-only extraction remains limited.
- Website facts, sourced workflow hypotheses, and unknown headcount/country.
- Structured Person name/role/email linkage; optional Hunter personal domain
  enrichment and independent verifier. No key means no independent verification,
  so such contacts cannot pass qualification. No inferred-address generation.
- Strict rejection without supported company identity, US location, service fit,
  an evidenced workflow, and a named relevant independently verified contact.
- SQLite records, evidence, suppressions, checkpoints, transactional daily call
  and estimated-cost limits, metrics, single-worker lease, and staging outbox.
- Existing Prospect Pipeline schema read-only sync. ALL existing company/email
  matches block repeat staging. Sent/opt-out statuses remain suppressed.
- Review-tab-only idempotent staging: re-read IDs each time, RAW cells, no blind
  append retries. Outbox remains pending after uncertain writes for reconciliation.
- Worker schedule code exists but defaults off. `/data` volume required.
- Evidence model interface accepts an injected cloud/local JSON completion call;
  output can supply hypotheses only, never factual contact fields.

## Commands (repository root)

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python tests/fixture_crawl.py
python tests/fixture_pipeline.py
python -m lead_agent run --config config/targets.example.json --dry-run
```

Dry-run has zero provider calls and zero database writes. The example targets
are illustrative; copy to ignored `config/targets.json` and set your intended
industries/geographies. There is no requirement to supply business URLs.

An explicitly authorized live run uses the same command with `--execute`. Without it, the default is a zero-call dry-run.
Credentials are read from process environment (or Cloud settings for the Scrapy
prototype). The CLI intentionally does not silently load another project's .env.
Set LEAD_AGENT_DB to an absolute path on the worker's mounted `/data` volume.
Use `python -m lead_agent metrics` for aggregate quality/budget counters.
Use `python -m lead_agent suppress --kind email --value ADDRESS --reason REASON`
to persist an opt-out. Domain suppressions are also supported.

After live tests, `python -m lead_agent stage` may write QUALIFIED records only
to an existing dedicated Lead Review tab. It requires service account credentials,
sharing on the actual spreadsheet, and the verified spreadsheet ID in private env.
It first reads Qualified Prospects using its inspected 14-column schema.
The live Qualified Prospects tab is prohibited as a write target.

## Provider contracts

Zyte Search: https://docs.zyte.com/zyte-api/usage/search/index.html
Hunter: https://hunter.io/api-documentation/v2
Headers carry Hunter credentials; API keys never appear in query strings/logs.
Hunter accept_all/unknown/webmail/disposable/invalid/pending results are NOT
independent mailbox verification. Even a valid result needs a fresh verification
date, positive SMTP check, no accept-all, and no block/disposable flags.
Verification is a provider observation, never a delivery guarantee.

No Firecrawl runtime dependency, Google Places adapter, LinkedIn/Apollo scraper,
or other enrichment provider is claimed implemented. Additional adapters can be
added behind the same persistence and verification gates when configured.

## Limits that remain before production

Live Zyte Search domain/account compatibility, Hunter data quality, Google service
account permissions, and the complete end-to-end staging path are NOT yet tested.
The existing campaign's full reply/opt-out state feed must be made available
read-only before an approval-based intake handoff can be enabled. Current Sheet
status alone cannot prove campaign-state coverage. No live handoff/send command
exists; staged rows cannot start the campaign.

Cross-business duplicate contacts are rejected; every company is saved once by
registered domain. Research checkpoints preserve partial progress. Explicit rejected-only re-research/versioning is implemented; reviewed-record
re-review and records absent from current discovery results remain future work. Current job leases assume one host with a
shared SQLite volume and a maximum 10-minute run, not distributed execution.

Daily cost fields are configurable ESTIMATES, not provider price guarantees or
invoice caps. Retry attempts consume budget; separate provider limits apply.
Set account-side spending caps as well. No provider subscriptions were purchased.

Do not point production Railway at this branch or enable ENABLE_SCHEDULE until
controlled testing passes and the user explicitly authorizes deployment.

## Session continuation — October 20, 2026

Fetched `dev/autonomous-lead-pipeline` at `42e73a5` and preserved the earlier
independent Phase 1 branch as `archive/session-phase1-discovery` (tip `622e647`).
The branches share only the initial commit. This branch's runner is the primary
implementation; the older OSM package and its local data remain preserved on the
archive branch, rather than adding a second competing pipeline. No history was
reset or force-pushed.

Explicit refresh is now available via an optional target configuration field:
`"refresh_rejected_before": "2026-10-01T00:00:00+00:00"`.
It only refreshes previously rejected, unreviewed records older than that cutoff
that reappear in bounded discovery. The cutoff must be timezone-aware and not
in the future. Leave it absent to keep normal skip-known behavior. Refresh uses
its own checkpoint generation, snapshots immutable prior/current payloads in
`company_versions`, and uses an optimistic generation check inside the save
transaction. Reviewed/approved/qualified/queued records and suppressions cannot
be refreshed by this path. Historical contact identities remain reserved to
prevent reassignment to another business. Prior evidence remains available in
version payloads; the existing evidence table remains an append-only union.
This does not yet refresh all database records independently of search results,
or provide an approved re-review path for previously qualified records.

Interrupted enrichment now checkpoints successful Hunter contact discovery and
individual verification results per research generation. Restart reuses fresh
verified results and permanently invalid results; expired verification is
retried within existing budgets. A new explicit research generation may verify
again. Provider failures/pending/unknown results are not treated as fresh proof.
Page-by-page website recovery is still a remaining task.

Qualification and review staging now require verification method, checked time,
and provider verification date no older than 30 days and not in the future.
Staging rechecks qualification without changing saved evidence and reports
`verification_or_qualification_blocked` for stale/incomplete records. Such rows
remain pending, and the staging CLI returns failure. Hunter webmail responses
cannot earn independent verification. No new live handoff or send path exists.

Checks: baseline 41 pytest tests + 14 subtests and four MCP tests passed after
installing this branch's existing requirements in the repository-local venv.
Updated suite: 65 tests + 14 subtests passed. Both real-engine offline fixtures
and all four MCP unit tests passed. No paid provider calls, live Sheets writes,
scheduling, deployment, or campaign operations were performed. GitHub Actions
and live provider data quality were not validated in this session.

Rollback: these changes are development-only. Revert the continuation commit
on this branch to restore the previous code; never reset production services.
The additive company_versions table can remain when rolling code back. Do not
remove it or restore an old database over newer evidence. Back up SQLite using
its backup API before any future approved migration/deployment. No existing
session database was opened or migrated to test this change; all tests used
synthetic temporary databases.

OAuth investigation (read-only, separate MCP service only): Railway service
293086b8-ddfb-457a-b892-7d8b79d2d98b is live on feat/zyte-mcp with one replica
and no volume. Logs show an old token rejected for a missing JTI mapping, then
multiple GitHub callbacks (302) followed by ChatGPT token exchanges (200).
This does not prove the final ChatGPT connection was recorded. User reports
"missing OAuth callback data"; exact failing page is still needed. A separate
startup diagnostic reports Scrapy Cloud jobq HTTP 403. No credentials were
read or changed and no service was restarted/redeployed. Do not conflate that
upstream authorization issue with the final OAuth connection error.

## Scrapy 2.19 compatibility

Updated requirements-crawler.txt to Scrapy>=2.19,<2.20 after testing 2.19.0
on Python 3.12/Linux. All 65 pytest tests and 14 subtests pass. Both real-engine
offline crawl fixtures pass. Fixture download handlers now provide close();
Scrapy remote control is explicitly disabled alongside the existing telnet
console, so no extra local control server/state directory is started.
No crawler extraction logic, deployment, or campaign code was changed. Windows
Python 3.13 and Scrapy Cloud deployment were not executed here. The direct
scrapy-zyte-api plugin is a separate integration and has not been added or
live-tested by this version compatibility change.

## Full autonomous requirement continuation

See AUTONOMOUS_AGENT_REQUIREMENTS.md for the complete implementation/validation
matrix and RUNBOOK.md for Windows setup, direct transport, local review and
rollback. The agent defaults to dry-run and requires --execute for provider work.
Added configured role/email/domain exclusions; persisted per-job retry state and
metrics; per-page restart checkpoints; read-only campaign snapshot suppression;
version-bound explicit local approvals and a no-write/no-send handoff preview.
Added source-attributed operational signal analysis and optional localhost Ollama
hypotheses that cannot boost scores or create contacts. Added opt-in
scrapy-zyte-api 0.36 transport with four requests maximum and one SDK attempt.

Final local checks on Python 3.12/Linux with Scrapy 2.19.0: 83 pytest tests +
14 subtests, four MCP tests, both offline crawler fixtures, pip check, source
distribution build and whitespace check passed. Direct transport settings and
classes were checked without a live request. Windows Python 3.13, live Zyte/
Hunter responses, Ollama inference, campaign export completeness, Google writes,
CI on GitHub, deployment and scheduling are not claimed tested. No paid provider
call, external sheet write, campaign import/send, or deployed-service change
was performed. The snapshot adapter does not establish that an arbitrary file
is authoritative; operational export coverage is a production acceptance gate.

## Bounded acceptance harness

Added lead_agent.validation: a zero-call default plan plus explicitly approved
six-attempt live smoke against two industries. Fresh isolated local state avoids
mistaking cached fixtures for live results. No enrichment/model/Sheets/outreach/
deployment path is used. Five acceptance-harness tests cover dry-run/no DB side
effects, missing paid approval/credentials, plan protections, and exact six-call
execution against synthetic HTTP responses. Full suite: 88 tests + 14 subtests.
GitHub Actions for previous head b12bde9 completed successfully for both Crawler
checks (run 39525770353) and MCP checks (run 39525770230).

Live execution remains blocked on secure local ZYTE_API_KEY configuration and
explicit provider-cost authorization. Railway OAuth only exposes variable names;
the presence of its key does not imply access from this workspace. No paid calls
or external writes were performed. The $0.06 harness ceiling is estimated
accounting, not a provider invoice cap; credits-only usage cannot be guaranteed.

## First live Zyte bridge validation

After explicit owner approval for paid use within the six-request validation,
one call through the existing connected Zyte MCP app successfully fetched
https://aibudbots.ai/. This demonstrates that the existing MCP connection and its
Railway-side Zyte configuration can complete a page extraction. The local managed
workspace still has neither ZYTE_API_KEY nor OPENAI_API_KEY; no credentials were
copied/read from Railway. OpenAI was not called. This is not a successful run of
the two-industry Search acceptance harness, independent mailbox verification,
or a production readiness claim. No Sheets write, email, service configuration
change, scheduling, restart or deployment occurred.

The returned page exposed a deployed extractor defect: a demo example.com address
was included in its published contact list. mcp/core.py on this development branch
now rejects reserved example/test email domains from both mailto and visible-text
extraction while retaining source text. A dedicated regression test covers the
live failure pattern. Five MCP unit tests pass; the 88-test agent suite remains
passing. This fix is not deployed to the existing MCP service.
