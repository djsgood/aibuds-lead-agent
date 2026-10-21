# Daily discovery review

Scope: lead-agent repository and separate discovery-worker service only. The
outreach service and its volume, schedules, secrets and data are protected.

Findings corrected:
- Root Railway configuration selected the MCP web image for the worker. Worker
  releases now use an isolated pinned branch with the tested worker config at
  root; do not merge that release-only root config into the MCP branch.
- Export previously required industry evidence without target-city/state evidence.
  It now requires a sourced website mention of both city and state, emits the
  supporting quote/URL, and excludes unrelated or unsupported locations.
- Daily report queries now restrict both ends of the UTC date and report saved API
  attempt/estimated-cost counters. Repeated or overlapping jobs use leases and
  recheck the completion marker; restart recovery preserves SQLite state.
- Transient Zyte 520-class errors receive the existing bounded retry policy; each
  attempt is budgeted. Logged provider error categories are whitelisted paths.
- SQLite/report files are private. Reports are atomically replaced and CSV values
  are escaped against formula execution.

Deployment conditions:
- Live provider search must pass before cron is enabled. Initial live search
  returned HTTP 520 and zero leads; no successful daily list is claimed yet.
- Cron target: 13:00 UTC daily. Up to 50 candidates may be researched to produce a
  maximum 25 locally evidenced prospects within runtime/request budgets.
- $3/day is an estimated client accounting ceiling, not a guaranteed provider
  invoice ceiling. Railway storage/compute are separate charges.
- A shortage must remain a shortage; no invented contacts or filler records.
- Without an independent email verifier, contacts remain outreach-ineligible.
- Lists are private CSV/JSON files on /data/reports, not automatic email or Sheets
  delivery. No automatic campaign handoff or sending exists in the daily job.

Source review covered the runner, providers, research/qualification, SQLite jobs,
leases and budgets, daily exports, worker image, deployment configuration and
existing outreach handoff boundaries. Automated tests supplement this review;
GitHub checks are test workflows, not a Copilot review.
