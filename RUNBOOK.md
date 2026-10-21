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

## Bounded live validation

Use the dedicated harness rather than an unrestricted execution while validating
account access. Planning requires no credentials and performs no API/DB calls:

```powershell
.\.venv\Scripts\python -m lead_agent.validation
```

After explicitly approving provider charges and securely configuring ZYTE_API_KEY
in the environment where the command will run:

```powershell
.\.venv\Scripts\python -m lead_agent.validation --execute --allow-provider-charges
```

The harness uses new isolated local state on every execution, two industry targets
(roofing/plumbing in New Martinsville, WV), one business and one page per target,
and a maximum SIX total Zyte attempts including retries, search, and robots.
The accounting ceiling is $0.06 at a $0.01/attempt estimate, NOT an invoice or
provider-price guarantee. Actual pricing and account-side spending controls remain
necessary. Credits-only use cannot be guaranteed by this client; verify the account
balance/billing controls first. It does not use Hunter, Ollama, Google Sheets,
campaign APIs, scheduling or deployment. It writes private local records and a
contact-free summary to data/live-validation/<unique-run>/report.json.

Missing charge approval or credentials blocks before DB creation/provider calls.
The result includes source-backed identity/page/evidence counts, rejection reasons,
errors, actual attempted-call counters and estimated accounting. No contacts can
qualify for handoff without independent verification; a passing research smoke
never means production-ready. An incomplete sample or API/account/schema failure
returns nonzero, retains evidence and does not silently broaden the budget.

On October 20 the managed execution environment had no provider credentials.
The connected Railway OAuth app confirmed ZYTE_API_KEY exists on the separate MCP
service but exposes names only. It cannot transfer that key into this workspace.
No key was read/exposed and no live paid call was made. Configure the key through
secure environment settings or run this command locally where it is configured;
never paste keys into chat or commit them.

## Daily discovery worker

Deploy only `aibuds-discovery-worker` using `Dockerfile.worker`, pinned to a reviewed
commit on the dedicated `deploy/discovery-worker` branch. That release branch
copies `railway.worker.json` to root `railway.json`, because the current Railway API
rejects custom config paths. Never merge the worker root config into the MCP
development branch. The MCP keeps its existing branch and pinned commit.
Its one-shot command is `python -m lead_agent.daily`; Railway cron is
`0 13 * * *` (13:00 UTC daily), restart policy NEVER. Do not also run the older
looping worker on this service. Set ENABLE_SCHEDULE=true only after validation.

Use config/targets.daily.json: research up to 50 candidates to produce at most 25
industry- and location-supported prospects, one website page
plus robots per business, roofing/plumbing/HVAC in New Martinsville, Wheeling and Parkersburg WV; Marietta and Steubenville OH;
and Washington PA, as approved by the owner. The target
is not a guaranteed count of verified contacts. Unknown identities stay blank;
only sourced, industry-matching records appear in reports. Daily CSV and JSON
summaries live in /data/reports/YYYY-MM-DD.csv and .json, alongside persistent
/data/lead-agent.sqlite3. Reports never send emails or write campaign/Sheets data.
Reports contain published contacts and should remain private. No public domain
is needed. A date checkpoint prevents duplicate runs on the same UTC date.

Budget settings: LEAD_AGENT_MAX_DAILY_COST_USD=3,
LEAD_AGENT_MAX_DAILY_CALLS=100, LEAD_AGENT_ESTIMATED_CALL_COST_USD=0.03.
Each retry reserves another attempt. These are estimated accounting limits, not a
provider-side invoice guarantee; search request weight varies with maxResults.
Use Zyte account spending controls for a hard billing ceiling. Railway usage and
storage are separate. Keep the daily worker's key as a Railway variable reference
`${{aibudbots-zyte-mcp.ZYTE_API_KEY}}`; do not copy secret values into files.

Rollback: disable this worker's cron and set ENABLE_SCHEDULE=false, preserving its
volume. Re-pin its previous reviewed commit if needed. Never change the outreach
service or its volume. Do not delete the SQLite database to retry a job.

Daily list location evidence is a sourced website mention of the target city and
state, not independent headquarters verification. The CSV includes its quote and
source URL. Records without this evidence are excluded, even if search returned
them. Reports include UTC-day API attempt and estimated-cost totals.

## Working HTML discovery route

The daily worker uses ZYTE_SEARCH_MODE=html by default: POST /v1/extract fetches a
public Google results page; deterministic parsing extracts organic heading links
and published local-business website links. The separate /v1/search route remains
available via search mode api, but returned 520 /download/temporary-error in live
tests. One live /extract search-page request succeeded and returned relevant
Wheeling WV roofing businesses. This does not require Apollo or another plan.

HTML discovery filters directories, groups duplicate domains, preserves the
Google source URL and never converts search titles into verified business names.
Consent/challenge pages and unknown HTML fail clearly. It does not unlock hidden
Apollo/LinkedIn fields. Actual business research and location evidence still come
from the business website before daily-list inclusion.

Search results and the next-page cursor are atomically checkpointed per query.
Retries on the same UTC date reuse the cached response, and subsequent dates
advance by ten results, cycling after ten pages or resetting after a genuine
empty result. Each HTTP attempt still counts toward the persistent Zyte budget.
