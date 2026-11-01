# AIBudBots lead agent

Development of autonomous business discovery, evidence-backed research, contact
verification, qualification, persistence, and review-only Google Sheets staging.

Start with [ARCHITECTURE.md](ARCHITECTURE.md) for the inspected integration contract.
The existing outreach campaign must remain untouched.

[Full autonomous-agent requirements and status](AUTONOMOUS_AGENT_REQUIREMENTS.md)
| [Windows setup, execution, review, deployment and rollback](RUNBOOK.md).

`python -m lead_agent run --config config/targets.example.json` defaults to a
zero-call dry-run. Execution remains disabled until a replacement discovery
provider is explicitly connected.
