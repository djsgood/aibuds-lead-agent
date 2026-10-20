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
