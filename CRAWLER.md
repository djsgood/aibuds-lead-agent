# AIBudBots business contacts crawler

Repository: `djsgood/aibuds-lead-agent`.
Run commands from the repository root, which contains `scrapy.cfg`.
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
python -m pip install -r requirements-dev.txt
python -m scrapy list
python -m pytest tests -q
python tests/fixture_crawl.py
```

## First live crawl

```bash
python -m scrapy crawl business_contacts -a urls=https://aibudbots.ai -O reviewed-leads.json
```

`reviewed-leads.json` is still pending review despite the filename. Missing fields
remain empty. JavaScript-only pages and obfuscated contacts may not be extracted.

## Local spider arguments and discovery

For multiple sites, use a JSON array for `urls` (maximum 10). `max_sites` defaults
to 10 and `pages_per_site` to 3; they can be reduced but cannot exceed those bounds.
The example business_contacts spider is URL-driven. The retained lead_pipeline
spider supports industry/location discovery, but is an extraction prototype; use
`python -m lead_agent run --config config/targets.example.json` for the persistent
autonomous runner. It defaults to a zero-call dry-run. See RUNBOOK.md for optional
direct Zyte transport and explicit execution controls. No external job host is
required for local Scrapy crawling.
