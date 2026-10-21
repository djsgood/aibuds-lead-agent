# AIBudBots private Zyte MCP server

Repository: djsgood/aibuds-lead-agent
Branch: feat/zyte-mcp
Application code: mcp/
Docker build context: repository root
Scrapy Cloud project: 882321

## Capabilities
- collect_business_contacts: one explicit HTTPS page per invocation, published emails and phone links with source URLs. Contact and business names remain null rather than guessed.
- list_crawler_spiders: read-only listing for project 882321.
- No outreach, Sheets writes, automatic discovery, deployment of spiders, or job scheduling is implemented.
Use the existing Google Sheets ChatGPT connection separately after reviewing results.

## Deploy on Railway
Create a separate service in the existing lead agent Railway project. Use this repository, branch feat/zyte-mcp, repository root / and its Dockerfile. Do not change the existing aibuds service.
Before starting, set these service variables privately:
- ZYTE_API_KEY: your AIBUDS 1 Zyte API key.
- SCRAPY_CLOUD_API_KEY: your Scrapy Cloud account API key (optional for page collection).
- ALLOWED_GITHUB_LOGIN: djsgood.
- PUBLIC_BASE_URL: https://<generated-service-domain> with no trailing /mcp.
- GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET: create a GitHub OAuth App as below.
- JWT_SIGNING_KEY: generate once with python -c "import secrets; print(secrets.token_urlsafe(48))" and store privately.
Never paste secrets into chat, commit them, or put them in URLs.
PORT is supplied by Railway. One replica only.

## GitHub OAuth App
Visit https://github.com/settings/applications/new
Application name: AIBudBots Zyte MCP
Homepage URL: your generated Railway HTTPS base URL.
Authorization callback URL: that base URL + /auth/callback
Create the app, generate its client secret, and enter both values directly into the Railway service variables.
No repository scopes are requested; the OAuth app is for sign-in.

## Connect ChatGPT
After the service is healthy:
Name: AIBudBots Zyte
Connection: https://<generated-service-domain>/mcp
Authentication: OAuth
Sign in with GitHub as djsgood. Other identities cannot use the tools.
Test: ask to collect published contacts from ONE business URL.
A failed owner check should trigger investigation of token claim shape, never removal of authentication.

## Checks and limitations
GitHub Actions runs extraction, validation, mocked upstream tests, and imports the actual FastMCP server to discover tools.
A full live OAuth round trip and paid Zyte request still require your private credentials.
The /health endpoint reports process health only, not credential validity.
OAuth uses in-memory client/token storage. Restarting or redeploying can require reconnecting ChatGPT. Add encrypted persistent OAuth storage before multi-user or production customer use.
The request budget is 30 upstream requests/hour/process, not a durable billing cap. Set a spending limit in Zyte as well. Rejected attempts count.
Redirects are disabled; supply the final HTTPS business URL. Private/IP targets are rejected; URLs are fetched remotely by Zyte, never directly by this server.
Extraction can miss obfuscated contacts, images or JavaScript-only content; no deliverability or business identity guarantee.
This is a private first integration, not a finished lead-generation product.

## Diagnose Scrapy Cloud access independently of Zyte API

The successful Zyte API page test does not validate SCRAPY_CLOUD_API_KEY. A live
spider-list response returned HTTP 403 with `{"message":"Authentication failed","status":"error"}`. This is an authentication rejection, not proof of an invalid
spider, a broken MCP startup, or missing project permission alone. Never fix it
by disabling authentication, resetting a project or replacing ZYTE_API_KEY.

The development check now reads the actual spiders/list.json endpoint and
requires HTTP 200 plus status=ok and a spiders list before declaring access ready.
It distinguishes authentication_failed, access_denied, rate_limited, network_error
and response_schema_mismatch without logging upstream bodies/keys. Startup checks
are read-only and optional for standalone Zyte page extraction.

Use SCRAPY_CLOUD_PROJECT_ID to select the spider-list/diagnostic project (default
882321; pipeline project is 882346). It does not redirect scheduling/results
tools, which remain scoped to the existing pipeline project. Select only a project
you actually own. Correct account credentials and project membership still need
verification before any Cloud job is run.

In the separate Railway service aibudbots-zyte-mcp, replace only
SCRAPY_CLOUD_API_KEY with the API key from the owner's Scrapy Cloud account using
private variables. Do not paste it into chat or change the working ZYTE_API_KEY.
Connected Railway OAuth tools expose variable names only, so Codex cannot read
or repair the secret. Do not change the outreach service's variables or state.
After the owner applies a corrected key, retry the read-only spider listing; a
200/status=ok result is required. No job scheduling or paid Zyte calls are needed.

These source changes are on dev/autonomous-lead-pipeline. Do not claim the live
feat/zyte-mcp service is running them until an explicitly approved deployment is
verified. The service was not redeployed by this diagnostic work.
