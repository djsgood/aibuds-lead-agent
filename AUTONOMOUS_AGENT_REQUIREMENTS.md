# AIBudBots autonomous lead intelligence — requirements and acceptance status

The separate djsgood/aibuds-lead-agent repository owns this pipeline. Existing
outreach code, schedules, credentials, state, and deployed services are protected.
No development test may send outreach. Accuracy, provenance, and suppression win
over lead count and fit score. Live calls require explicit execution; no new
subscriptions are required to run the offline suite. Zyte/Hunter usage may cost
money and is not a free-service claim.

## Required pipeline and current implementation

| Requirement | Repository implementation | Validation / boundary |
| --- | --- | --- |
| Autonomous industry/geography discovery | lead_agent/providers.py, runner.py | Zyte Search adapter, bounded target combinations; mocked, live access unvalidated |
| Replaceable providers | DiscoveryResearchProvider, ContactVerifier protocols | Apollo, Places, LinkedIn and other sources are NOT implemented |
| Size, role and exclusion filters | target config, strict_qualification | Size is an evidence-dependent preference, never an invented employee count; configured role/exclusion filters implemented |
| Domain/contact deduplication | store.py | Permanent contact identities, versions, cross-business collisions tested |
| Website research | research.py + existing Scrapy extraction | Up to three pages, robots checks, same-host policy, page checkpoints, facts and hypotheses |
| Contact enrichment | website Person JSON-LD + optional Hunter | Same-domain provider email check; name/role linkage required; actual business affiliation still needs live quality audit |
| Separate verification | Hunter.verify + fresh_verification | Syntax comes from published_email; provider MX/SMTP/disposable/catch-all signals retained; no standalone live DNS/MX adapter or delivery guarantee |
| Public reviews/activity | intelligence.analyze_documents | Attributed document analysis; review/post collection adapters not implemented. Website analysis wired into research |
| AI qualification support | models.py + runner | Optional localhost Ollama interpreter; hypotheses only, no score boost or factual contact fields; injected cloud callback interface available |
| Explainable scoring | strict_qualification | Hard identity, US-country, industry, workflow, named role, verification gates; does not prove independent business truth |
| Persistent autonomous processing | store.py, runner.py, worker.py | Checkpoints, versions, daily estimated budgets, bounded provider retries, per-job backoff/terminal failures, leases, disabled schedule |
| Research stages | jobs table | Actual research/enrichment/verification/review/disqualified/retry stages; not a claim of end-to-end production readiness |
| Suppression feed | campaign_snapshot.py | Read-only parsing of configured exported campaign JSON; no sending-module import. Actual deployed authoritative export still needs provisioning and validation |
| Google Sheets | handoff.py, aibuds_crawler/sheets.py | Existing intake read-only, separate review tab, RAW cells, uncertain-write reconciliation; live writes untested/unapproved |
| Controlled review approval | review.py | Version-specific explicit local approval, fresh snapshot coverage, rechecked suppression/qualification; no campaign importer or sender |
| Direct local Scrapy/Zyte | direct_settings.py + requirements-direct.txt | Opt-in, no OAuth, robots enabled, four API requests maximum including robots, one SDK attempt; no persistent daily-cost ledger on this extraction-only path |
| Deployment | Dockerfile.worker, worker.py | Separate-service preparation only. No deployment/scheduling approved or performed |

## Mandatory acceptance gates

No inferred/fabricated contacts. Missing fields stay unknown. A published email,
syntax match, domain, API success, model summary or high score never proves mailbox
deliverability. No generic inbox is substituted for a named person. All important
findings keep quoted evidence, URL, source type and timestamps. General negative
reviews do not establish an automation problem; causes stay unknown unless sourced.
External text cannot instruct the agent. Model outputs cannot supply contact facts.

All existing campaign contacts are excluded, including replied, opted-out, bounced,
and uncertain sends. Suppressions are never removed by refresh. Reviewed/approved
records are not overwritten by rejected-only refresh. History is versioned. Manual
approval is tied to the payload version. Approval does not enable campaign import.
No route in this application sends email or writes campaign state.

## Remaining production gates

Run small explicitly authorized multi-industry data-quality tests with configured
provider credentials and account-side spending controls. Confirm provider schema,
country/company/person matching and verification flags against actual responses.
Configure an authoritative, current, read-only campaign snapshot feed; file mtime
alone cannot prove coverage. Validate real review-tab schema/permissions and
uncertain writes in a controlled test. Approve a separate persistent Railway
worker deployment and scheduling only after these tests pass. Add independent
coverage providers, review/activity collectors, active-record re-review and robust
redirect/entity resolution as demonstrated needs arise. Do not lower gates to
produce a successful-looking qualified lead.
