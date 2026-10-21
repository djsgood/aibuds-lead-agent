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

An explicitly authorized live run uses the same command without `--dry-run`.
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
registered domain. Research checkpoints preserve partial progress. Explicit
re-research/versioning of stale rejected records is still required before
indefinitely recurring production use. Current job leases assume one host with a
shared SQLite volume and a maximum 10-minute run, not distributed execution.

Daily cost fields are configurable ESTIMATES, not provider price guarantees or
invoice caps. Retry attempts consume budget; separate provider limits apply.
Set account-side spending caps as well. No provider subscriptions were purchased.

Do not point production Railway at this branch or enable ENABLE_SCHEDULE until
controlled testing passes and the user explicitly authorizes deployment.
