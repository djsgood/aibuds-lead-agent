# Run the autonomous lead agent

Run from the aibuds-lead-agent repository. Python 3.12 is locally tested; Windows
3.13 remains user-side validation. No MCP connection or GitHub OAuth is needed.

## Windows PowerShell setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt -r requirements-direct.txt
Copy-Item config\targets.example.json config\targets.json
$env:LEAD_AGENT_DB = Join-Path $PWD 'data\lead-agent.sqlite3'
.\.venv\Scripts\python -m lead_agent run --config config\targets.json
```

The last command defaults to dry-run: zero API calls and zero DB writes. Configure
industries, locations, maximum businesses/pages/contacts, buyer-role preferences
and excluded domains/emails in the private target file. Employee range earns
preference only where evidence exists; unknown remains unknown.

Set credentials privately in environment. For a masked Zyte key prompt:

```powershell
$zyteSecret = Read-Host 'Zyte API key' -AsSecureString
$env:ZYTE_API_KEY = [System.Net.NetworkCredential]::new('', $zyteSecret).Password
```

Set HUNTER_API_KEY similarly if licensed verification is available. Without it,
no contact qualifies as independently verified. Keys are never requested in chat.
Configure daily call and estimated-cost limits from .env.example as environment
variables. The application does not auto-load another project's .env. Estimated
costs are accounting estimates; set account-side billing controls too.

Only when provider calls are authorized:

```powershell
.\.venv\Scripts\python -m lead_agent run --config config\targets.json --execute
.\.venv\Scripts\python -m lead_agent metrics
```

The runner independently discovers websites from industry/location queries,
researches, enriches, verifies, qualifies, and stores review-ready or rejected
records. It does not require seed websites. Restart repeats the same command:
completed checkpoints and fresh verification are reused, suppressions remain,
and persistent failures back off before becoming terminal after three failures.
An explicit refresh_rejected_before timezone-aware cutoff in target config creates
a new version for eligible stale rejected records that reappear in discovery.

## Optional direct Scrapy extraction

This is a bounded component for testing a supplied website, not the autonomous
industry/location runner. It uses the locally installed scrapy-zyte-api transport.
SDK retries are limited to one attempt; maximum four requests including robots.
No Sheets writes occur in the business_contacts spider. No daily ledger is shared
with the autonomous runner, so do not use this path for unattended schedules.

```powershell
$env:SCRAPY_SETTINGS_MODULE = 'lead_agent.direct_settings'
.\.venv\Scripts\python -m scrapy crawl business_contacts -a urls=https://YOUR-BUSINESS-DOMAIN -a pages_per_site=1 -O data\direct-contacts.json
Remove-Item Env:SCRAPY_SETTINGS_MODULE
```

This makes paid API requests if executed. No live direct-provider test was run by
Codex. The default crawler and complete autonomous runner keep their existing
separate transports; direct_settings is explicitly opt-in.

## Local review and suppression

Obtain a verified, current read-only export of the authoritative campaign's JSON
state through your existing administration process. Do not run/reset/import the
campaign to produce a test snapshot. Mount or copy that export read-only; this
agent never edits it. Then, with the lead-agent DB selected:

```powershell
.\.venv\Scripts\python -m lead_agent sync-campaign --snapshot PATH-TO-READONLY-SNAPSHOT.json
.\.venv\Scripts\python -m lead_agent approve --lead-id LEAD-ID --reviewer YOUR-NAME
.\.venv\Scripts\python -m lead_agent handoff-preview
```

Snapshot must be nonempty, valid, modified within 24 hours; sync must be within
one hour before preview/approval. These checks do not independently establish
export completeness. Preview prints counts only, never sends/imports or exposes
raw contact records. Approvals bind to the exact stored payload. New suppression,
verification expiry, or payload changes block it. A live campaign handoff remains
disabled. Explicitly authorized `python -m lead_agent stage` writes only to the
existing dedicated Lead Review tab; configure its Google service account and
spreadsheet environment first. No stage write is authorized just by this runbook.

## Scheduling, deployment, rollback

ENABLE_SCHEDULE defaults false. The prepared worker requires a separate service,
/data volume, private credentials and target config; minimum interval one hour.
Keep the existing outreach service and existing MCP service source branches and
variables unchanged. Dockerfile.worker is for the new worker only. Do not deploy
or enable it until controlled live validation and explicit owner approval.

For rollback, disable a future approved worker before reverting its code; do not
roll back campaign state or delete the lead database. Back up SQLite via its backup
API (including consistent WAL state) before upgrading. New jobs/version/approval
tables are additive and may remain if reverting this code. Research is private:
protect the volume/snapshot, restrict file access, and never commit data/ or keys.

## Test commands

```powershell
.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python -m unittest discover -s mcp -v
.\.venv\Scripts\python tests\fixture_crawl.py
.\.venv\Scripts\python tests\fixture_pipeline.py
.\.venv\Scripts\python -m pip check
```

All external services are mocked in these tests. Offline success is not live
provider, account, Windows, Google, deployment or commercial-readiness proof.
See AUTONOMOUS_AGENT_REQUIREMENTS.md for feature status and remaining gates.
