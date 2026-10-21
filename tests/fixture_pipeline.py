"""Run discovery -> website -> qualification -> Sheets against offline fixtures."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrapy import signals
from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse, TextResponse, Response
from scrapy.utils.project import get_project_settings
from aibuds_crawler.spiders.lead_pipeline import LeadPipelineSpider
from aibuds_crawler.sheets import ReviewSheetsPipeline, ReviewSheet, HEADERS

saved, items = [], []


class FakeResponse:
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload


class FakeSheetsSession:
    def request(self, method, url, **kwargs):
        if method == "GET":
            return FakeResponse({"values": [HEADERS]})
        assert method == "POST" and url.endswith(":append")
        assert kwargs["params"]["valueInputOption"] == "RAW"
        saved.extend(kwargs["json"]["values"])
        return FakeResponse({"updates":{"updatedRows":1}})
    def close(self): pass


class FixtureSheetsPipeline(ReviewSheetsPipeline):
    def open_spider(self, spider):
        self.sink = ReviewSheet("x"*30, "Lead Review", FakeSheetsSession())
        self.sink.prepare()


class FixtureHandler:
    def download_request(self, request, spider):
        from twisted.internet.defer import succeed
        if request.url == "https://api.zyte.com/v1/search":
            assert "roofing" in json.loads(request.body)["query"]
            body = json.dumps({"status":"success", "url":"https://www.google.com/search?q=roofing",
                "organicResults":[{"url":"https://roofbusiness.com/", "title":"Roof Business"},
                                  {"url":"https://www.roofbusiness.com/", "title":"Duplicate"},
                                  {"url":"https://yelp.com/biz/sample", "title":"Directory"}]}).encode()
            return succeed(TextResponse(request.url, request=request, body=body, encoding="utf-8"))
        if request.url.endswith("robots.txt"):
            return succeed(Response(request.url, request=request, body=b"User-agent: *\nAllow: /"))
        if request.url.endswith("/contact"):
            body = '<body>Roofing New Martinsville <a href="mailto:Owner@roofbusiness.com">Email</a><a href="tel:+13045551234">Call</a></body>'
        else:
            body = '<body>Roofing in New Martinsville. Request a quote. jamie@example.com <a href="/contact">Contact</a><script type="application/ld+json">{"@type":"RoofingContractor","name":"Roof Business"}</script></body>'
        return succeed(HtmlResponse(request.url, request=request, body=body.encode(), encoding="utf-8"))


os.environ["ZYTE_API_KEY"] = "offline-fixture-key"
settings = get_project_settings()
settings.set("DOWNLOAD_HANDLERS", {"https": FixtureHandler}, priority="cmdline")
settings.set("ITEM_PIPELINES", {FixtureSheetsPipeline:300}, priority="cmdline")
settings.set("DOWNLOAD_DELAY", 0, priority="cmdline")
settings.set("LOG_LEVEL", "ERROR", priority="cmdline")
process = CrawlerProcess(settings)
crawler = process.create_crawler(LeadPipelineSpider)
crawler.signals.connect(lambda item, **kw: items.append(dict(item)), signal=signals.item_scraped, weak=False)
process.crawl(crawler, industries="roofing", locations="New Martinsville, WV")
process.start()
assert crawler.stats.get_value("finish_reason") == "finished", crawler.stats.get_stats()
assert len(items) == 1 and len(saved) == 0, (items, saved)
lead = items[0]
assert lead["emails"][0]["email"] == "owner@roofbusiness.com", lead
assert lead["qualification"] == "potential_fit", lead
assert lead["sheet_status"] == "not_qualified_not_written", lead
assert not lead["outreach_approved"]
assert len(lead["source_urls"]) == 2
print("Offline crawl passed: discovery -> deduplication -> collection -> research ranking; unverified prospect correctly refused Sheets staging.")
