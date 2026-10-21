# Autonomous lead agent — development contract

Primary repository: djsgood/aibuds-lead-agent.
Development branch: dev/autonomous-lead-pipeline.
No production deployment, scheduling activation, email sending, campaign-state
mutation, or outreach-repository mutation is authorized by this implementation.

## Phase 0 inspection

The original business_contacts spider is a working URL-to-contact component.
It is not a complete lead generator. Existing MCP tools collected one page and
listed Cloud spiders. The development lead_pipeline prototype adds discovery and
research but its original score was not sufficient to qualify a sales lead.

Read-only inspection of djsgood/aibuds/main confirmed its lead_pipeline.py uses an
external Apps Script endpoint with multiple attempted import actions. The external
Apps Script source and current campaign-state suppression feed are not in this
repository. We will not call those import actions or assume their behavior.

Read-only inspection of the actual AIBudBots Prospect Pipeline confirmed a single
tab, Qualified Prospects, with these 14 headers:

Date Added | Company | Industry | Location | Website | Decision Maker | Role |
Email | Phone | Why AIBudBots Fits | Evidence / Notes | Qualification Status |
Outreach Status | Last Contact / Next Step

Existing records include Not contacted and sent statuses. The new agent must
exclude *all* existing normalized email/domain matches from new intake, import
contacted/opt-out states as permanent suppressions, and never write that live tab
without a future explicitly authorized, idempotent approval handoff.

## Architecture

One Python application: provider adapters -> evidence/research -> contacts and
verification -> explicit qualification -> SQLite -> review-only Sheets outbox.
Scrapy remains an extraction engine, not a separate microservice requirement.
Railway worker uses a persistent /data volume for SQLite. Cloud jobs are useful
for crawling but their ephemeral filesystem must not be the only state store.

No Firecrawl runtime dependency. Discovery uses a bounded Zyte Search adapter.
The source adapter contract also permits directory, Google Places, and licensed
enrichment providers. An unavailable provider must report unavailable, not return
invented or silently "verified" contacts. Do not use intrusive scans or access
controls bypasses to obtain contact data.

Research stores source URLs, quotes, retrieval timestamps, and fact/hypothesis/
unknown labels. A request-a-quote form is an observed workflow signal, not proof
that the business misses leads. 5–50 US employees is a configurable preference;
unavailable headcount remains unknown and cannot earn headcount points.

Decision-maker emails must be explicitly associated with a named person and
relevant role by evidence. Role inboxes stay separate. Publicly published,
DNS/domain-validated, independently mailbox-verified, inferred, catch-all,
invalid, and unavailable verification results remain distinct. DNS presence does
not prove a mailbox exists. Guessed/catch-all/unverified addresses cannot qualify
for automated handoff.

## Persistence and reliability

SQLite stores runs, companies, contacts, evidence, suppressions, daily provider
budgets, source checkpoints, and staging outbox records. Domain/email keys are
normalized. Transactional claims and checkpoints prevent repeated work; a crash
must retain completed evidence. Every retry consumes budget before an attempt.
Daily call and estimated-cost limits are hard bounds; actual provider invoices
remain authoritative. Suppression always wins over prior approval.

Model interface accepts evidence and may return classifications/hypotheses only;
it cannot create authoritative names/emails or verification results. Cloud/local
model backends are optional. Deterministic validation gates remain mandatory.

## Delivery phases and truthful status

0. Inspection and architecture: grounded in current source/schema.
1. Discovery adapter, structured records, SQLite, dedup, dry run, CLI, tests.
2. Evidence-based website research and opportunity interpretation.
3. Person/role linkage, enrichment adapters, independent verification.
4. Explainable scoring, hard rejection rules, priority metrics.
5. Existing-sheet/suppression sync, staging outbox, approval-gated handoff.
6. Scheduled worker, metrics, budgets, restart recovery, deployment preparation.

Production readiness additionally requires controlled live data-quality tests,
an authoritative suppression feed, destination credentials, and explicit approval
before enabling scheduled deployment. Mocked tests are not live validation.
