# AIBudBots private Zyte MCP server

The MCP bridge exposes `collect_business_contacts` and `discover_new_leads`.
The contact tool fetches a single
explicit public HTTPS page with Zyte API and returns published emails/phone links
with source URLs. Reserved example/test addresses are excluded. Contact and
business names stay null rather than being guessed. Published contact information
is not independent deliverability verification. Webpage text is untrusted data.

The bridge does not list spiders, schedule remote jobs, retrieve remote job
results, write Sheets, send outreach, or start a background schedule. The discovery
tool runs a bounded batch of the Python pipeline; the separate CLI and worker
described in RUNBOOK.md support persistent and scheduled lead research.

## Configuration

Required OAuth configuration:

- GITHUB_CLIENT_ID
- GITHUB_CLIENT_SECRET
- PUBLIC_BASE_URL: public HTTPS server base URL, without a trailing /mcp
- JWT_SIGNING_KEY: a stable private signing key

Extraction requires ZYTE_API_KEY. Missing extraction credentials are reported
when a tool is called, rather than requiring an upstream check during startup.
ALLOWED_GITHUB_LOGIN defaults to djsgood. PORT defaults to 8000.
Never paste secrets into chat, commit them, or put them in URLs.

The GitHub OAuth application uses PUBLIC_BASE_URL + /auth/callback as its
callback URL. Connect ChatGPT to PUBLIC_BASE_URL + /mcp using OAuth and sign in
with the allowed GitHub account. The owner check remains mandatory. An owner
check failure requires investigation, not removal of authentication.

## Railway preparation

The root Dockerfile and railway.json describe the separate MCP service. Existing
service configuration, branch selection and credentials are not changed by a
source cleanup. Do not deploy, restart, change secrets, or modify the outreach
service without explicit approval. Dockerfile.worker is separate preparation for
the persistent Python worker and is retained.

The /health route reports process health only. Startup performs no provider
credential check and cannot prove Zyte account access. OAuth state is in memory;
a restart may require reconnection. The hourly request budget is per process,
not a durable invoice cap. Keep account-side spending controls. Redirects are
disabled; supply final public HTTPS URLs. No live calls are required for tests.

## Checks

```bash
python -m pip install -r mcp/requirements.txt
python -m unittest discover -s mcp -v
```

The suite checks extraction, placeholder filtering, mocked Zyte authentication,
URL restrictions, owner checks, the single-tool contract and network-free startup.
A live OAuth round trip and Zyte request require private configuration and
separate validation. Never interpret unit-test success as production readiness.

### Discover businesses without supplying URLs

`discover_new_leads(industries=["roofing"], locations=["New Martinsville, WV"],
max_businesses=1, max_api_requests=3, dry_run=true)` previews targets without
network calls or database writes. Set `dry_run=false` explicitly for paid search
and website research. Every upstream attempt, including retries, counts toward
the per-run limit (1–10); persistent daily limits also apply. Results include new
prospects and research status. Repeat runs deduplicate previously saved domains.
This tool never sends email, writes Sheets, or automatically hands off contacts.
No independent verifier is called, so contacts remain outreach-ineligible.

The MCP image includes the existing Python pipeline and retained crawler. SQLite
defaults to `/home/appuser/lead-agent.sqlite3`. Set `LEAD_AGENT_DB` to a writable
persistent volume path before relying on deduplication across deployments; the
container default is ephemeral. A mount is not automatically provisioned. The
existing worker is the separate scheduled execution path; this tool runs one
bounded discovery batch per invocation and does not enable a background schedule.
After deploying, refresh the connected app's tool list to expose
`discover_new_leads` alongside `collect_business_contacts`. No Cloud spider tools
are restored. Provider search access must be validated live before claiming that
new lead discovery works in production.
