# AIBudBots business contacts crawler

Repository: `djsgood/aibuds-lead-agent`; branch: `feat/zyte-mcp`.
Run commands from the repository root, which contains `scrapy.cfg` and `scrapinghub.yml`.
Crawler code: `aibuds_crawler/`. The Railway MCP code remains in `mcp/`.

## What it does

Spider name: `business_contacts`. Supply final HTTPS business website URLs.
It accepts at most 10 distinct hosts and fetches up to 3 HTML pages per host
(the supplied page plus contact/about/team links). Robots requests and one retry
can add HTTP requests beyond that page budget. Redirects are disabled: use final
URLs, including www when required. It respects robots.txt and uses low concurrency.

Each page produces one review record with published emails, phone links, and
source URLs. Duplicate emails/phones within a page and duplicate seed hosts are
removed. The same contact can appear on different pages to preserve its evidence;
merge records by email before importing to a CRM. Example/test addresses are excluded.
Business names are taken only from Organization/LocalBusiness/Corporation JSON-LD;
contact names remain null. Names are website claims, not independently verified.
No guessed emails, deliverability verification, outbound messages, Sheets writes,
automatic business discovery, or scheduled runs are implemented.

## Local test

```bash
python -m pip install -r requirements-crawler.txt
python -m scrapy list
python -m unittest discover -s tests -v
python tests/fixture_crawl.py
```

## First live crawl

```bash
python -m scrapy crawl business_contacts -a urls=https://aibudbots.ai -O reviewed-leads.json
```

`reviewed-leads.json` is still pending review despite the filename. Missing fields
remain empty. JavaScript-only pages and obfuscated contacts may not be extracted.

## Scrapy Cloud deployment

In project **882321**, select repository `djsgood/aibuds-lead-agent`, branch
`feat/zyte-mcp`, and repository root. Redeploy the latest commit using the same
GitHub deployment screen used previously. This is a standard Scrapy deployment,
not a custom image deployment; the root Dockerfile is for Railway only.
`scrapinghub.yml` identifies project 882321 and the crawler requirements.
The project-selected Cloud stack must support Python 3.8+ and the specified Scrapy
version range; no unverified stack identifier is hardcoded.

After deployment, run `business_contacts` with spider argument:

```text
urls = https://aibudbots.ai
```

For multiple sites, use a JSON array for `urls` (maximum 10). `max_sites` defaults
to 10 and `pages_per_site` to 3; they can be reduced but cannot exceed those bounds.
Review output in the Cloud job's Items tab. Running the crawler directly in Cloud
does not require the MCP API authentication issue to be solved first.

CLI alternative, when account API authentication works: install `shub`, use
`shub login` privately, then `shub deploy 882321`. Never commit account keys.
