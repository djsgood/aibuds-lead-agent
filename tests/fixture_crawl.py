"""Exercise the real Scrapy engine with in-memory HTTP fixtures; no network."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse, Response
from scrapy import signals
from scrapy.utils.project import get_project_settings
from aibuds_crawler.spiders.business_contacts import BusinessContactsSpider


class FixtureHandler:
    def download_request(self, request, spider):
        from twisted.internet.defer import succeed
        if request.url.endswith("robots.txt"):
            return succeed(Response(request.url, body=b"User-agent: *\nAllow: /", request=request))
        html = '<body>owner@business.com jamie@example.com <a href="/contact">Contact</a></body>'
        if request.url.endswith("/contact"):
            html = '<body><a href="mailto:help@business.com">Email</a></body>'
        return succeed(HtmlResponse(request.url, body=html.encode(), encoding="utf-8", request=request))


settings = get_project_settings()
settings.set("DOWNLOAD_HANDLERS", {"https": FixtureHandler}, priority="cmdline")
settings.set("DOWNLOAD_DELAY", 0, priority="cmdline")
settings.set("LOG_LEVEL", "ERROR", priority="cmdline")
rows = []
process = CrawlerProcess(settings)
crawler = process.create_crawler(BusinessContactsSpider)
crawler.signals.connect(lambda item, **kw: rows.append(dict(item)), signal=signals.item_scraped, weak=False)
process.crawl(crawler, urls="https://business.com")
process.start()
assert crawler.stats.get_value("finish_reason") == "finished", crawler.stats.get_stats()
assert len(rows) == 2, rows
assert {e["email"] for row in rows for e in row["emails"]} == {"owner@business.com", "help@business.com"}, rows
assert all(row["review_status"] == "pending_review" for row in rows)
print("Offline engine crawl passed: 2 pages, source URLs, example filtering, review-only output.")
