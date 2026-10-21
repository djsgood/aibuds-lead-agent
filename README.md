# AIBudBots lead agent

Development of autonomous business discovery, evidence-backed research, contact
verification, qualification, persistence, and review-only Google Sheets staging.

Start with [DEVELOPMENT.md](DEVELOPMENT.md) for commands, validation status, and
remaining production gates. [ARCHITECTURE.md](ARCHITECTURE.md) records the inspected
integration contract. The existing outreach campaign must remain untouched.

The original Scrapy crawler instructions are in [CRAWLER.md](CRAWLER.md).
This branch is not production-ready or approved for deployment.

[Full autonomous-agent requirements and status](AUTONOMOUS_AGENT_REQUIREMENTS.md)
| [Windows setup, execution, review, deployment and rollback](RUNBOOK.md).

`python -m lead_agent run --config config/targets.example.json` now defaults to
a zero-call dry-run. `--execute` explicitly enables budgeted research only.
Default discovery uses non-Zyte Open Web search/crawl (`LEAD_AGENT_DISCOVERY_PROVIDER=open_web`); `zyte` and `vibe_prospecting` remain optional provider modes.
