"""A small, review-only contact crawl from explicit business website seeds."""
import json
from urllib.parse import urlsplit
import scrapy
from aibuds_crawler.guard import public_url
from lead_agent.pipeline_utils import EMAIL, EXAMPLE_DOMAINS, contact_page_urls, extract_contact_values, published_email

__all__ = ["BusinessContactsSpider", "EMAIL", "EXAMPLE_DOMAINS", "published_email"]


class BusinessContactsSpider(scrapy.Spider):
    name = "business_contacts"

    def __init__(self, urls=None, max_sites=10, pages_per_site=3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_sites = int(max_sites)
        self.pages_per_site = int(pages_per_site)
        if not 1 <= self.max_sites <= 10 or not 1 <= self.pages_per_site <= 3:
            raise ValueError("max_sites must be 1–10; pages_per_site must be 1–3.")
        if not urls:
            raise ValueError("Provide urls as a JSON array or comma-separated HTTPS URLs.")
        values = json.loads(urls) if isinstance(urls, str) and urls.lstrip().startswith("[") else urls.split(",") if isinstance(urls, str) else urls
        if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
            raise ValueError("urls must be a list of website URLs.")
        self.seeds = {}
        for value in values:
            url = public_url(value)
            host = urlsplit(url).hostname
            self.seeds.setdefault(host, url)
        if not self.seeds or len(self.seeds) > self.max_sites:
            raise ValueError("Supply between 1 and max_sites distinct website hosts; never more than 10.")
        self.seed_hosts = set(self.seeds)
        self.scheduled = {host: {url} for host, url in self.seeds.items()}

    def start_requests(self):
        for host, url in self.seeds.items():
            yield scrapy.Request(url, callback=self.parse, errback=self.failed,
                                 meta={"business_host": host})

    async def start(self):
        for request in self.start_requests():
            yield request

    def failed(self, failure):
        self.crawler.stats.inc_value("business_contacts/request_failures")
        self.logger.warning("Page could not be collected: %s", failure.request.url)

    def parse(self, response):
        if not isinstance(response, scrapy.http.TextResponse):
            return
        host = response.meta["business_host"]
        visible = " ".join(response.xpath("//body//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript)]").getall())
        emails, phone_values = extract_contact_values(
            visible,
            response.css('a[href^="mailto:"]::attr(href)').getall(),
            response.css('a[href^="tel:"]::attr(href)').getall(),
        )
        # Schema.org Organization/LocalBusiness names only; no title-to-name guessing.
        business_name = None
        for text in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(text)
            except ValueError:
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in list(nodes):
                if isinstance(node, dict):
                    nodes.extend(node.get("@graph", []) if isinstance(node.get("@graph"), list) else [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                kinds = node.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if isinstance(kinds, list) and any(k in ("Organization", "LocalBusiness", "Corporation", "RoofingContractor", "Plumber", "Electrician", "HVACBusiness", "GeneralContractor", "HomeAndConstructionBusiness") for k in kinds) and isinstance(node.get("name"), str):
                    business_name = node["name"].strip()[:250] or None
                    break
            if business_name:
                break
        yield {"website": self.seeds[host], "business_name": business_name,
               "business_name_source_url": response.url if business_name else None,
               "contact_name": None, "page_title": response.css("title::text").get(),
               "emails": [{"email": e, "source_url": response.url} for e in emails],
               "phones": [{"phone": v, "source_url": response.url} for v in phone_values],
               "source_url": response.url, "review_status": "pending_review",
               "verification": "published_only_not_deliverability_verified"}
        links = contact_page_urls(response.css("a::attr(href)").getall(), response.url,
                                  urlsplit(response.url).hostname)
        for url in links:
            if len(self.scheduled[host]) >= self.pages_per_site:
                break
            if url in self.scheduled[host]:
                continue
            self.scheduled[host].add(url)
            yield scrapy.Request(url, callback=self.parse, errback=self.failed,
                                 meta={"business_host": host})
