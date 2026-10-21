"""Discover, collect, rank, and save leads from industries and locations."""
import base64
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin
import scrapy
from scrapy.exceptions import CloseSpider
from scrapy.http import HtmlResponse
from aibuds_crawler.discovery import parse_list, business_url, domain_key, lead_id, qualify
from aibuds_crawler.spiders.business_contacts import BusinessContactsSpider
from aibuds_crawler.guard import public_url


class LeadPipelineSpider(BusinessContactsSpider):
    name = "lead_pipeline"
    custom_settings = {"ITEM_PIPELINES": {"aibuds_crawler.sheets.ReviewSheetsPipeline": 300}}

    def __init__(self, industries=None, locations=None, max_businesses=10, pages_per_site=3,
                 write_to_sheets="true", *args, **kwargs):
        # This spider discovers its own seeds. Never invoke the URL-input constructor.
        scrapy.Spider.__init__(self, *args, **kwargs)
        self.industries = parse_list(industries, "industries")
        self.locations = parse_list(locations, "locations")
        self.max_businesses, self.pages_per_site = int(max_businesses), int(pages_per_site)
        if not 1 <= self.max_businesses <= 50 or not 1 <= self.pages_per_site <= 3:
            raise ValueError("max_businesses must be 1–50; pages_per_site must be 1–3.")
        if len(self.industries)*len(self.locations) > 6:
            raise ValueError("At most six industry/location search combinations per run.")
        if str(write_to_sheets).lower() not in ("true", "false"):
            raise ValueError("write_to_sheets must be true or false.")
        self.write_to_sheets = str(write_to_sheets).lower() == "true"
        self.seeds, self.scheduled, self.records, self.pending = {}, {}, {}, {}
        self.seed_hosts, self.seen_domains = set(), set()
        self.search_failed = False

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        spider.api_key = os.environ.get("ZYTE_API_KEY") or crawler.settings.get("ZYTE_API_KEY")
        if not spider.api_key:
            raise ValueError("Configure ZYTE_API_KEY privately before running lead_pipeline.")
        spider.search_domain = crawler.settings.get("ZYTE_SEARCH_DOMAIN", "google.com")
        return spider

    def start_requests(self):
        for industry in self.industries:
            for location in self.locations:
                query = f'{industry} businesses in {location} official website contact -site:yelp.com -site:angi.com -site:facebook.com'
                payload = {"domain": self.search_domain, "query": query, "include": ["organic"], "maxResults": 20,
                           "queryParameters": {"style": "generic", "geolocation": "US", "locale": "en-US"}}
                token = base64.b64encode((self.api_key + ":").encode()).decode()
                yield scrapy.Request("https://api.zyte.com/v1/search", method="POST", body=json.dumps(payload),
                    headers={"Authorization": "Basic " + token, "Content-Type": "application/json"},
                    callback=self.parse_search, errback=self.search_error,
                    meta={"zyte_discovery": True, "dont_obey_robotstxt": True, "dont_retry": True,
                          "handle_httpstatus_all": True, "industry": industry, "location": location, "query": query})

    def parse_search(self, response):
        if response.status != 200:
            self.search_failed = True
            raise CloseSpider(f"discovery_http_{response.status}")
        try:
            data = response.json()
        except ValueError:
            raise CloseSpider("discovery_invalid_json")
        if data.get("status") != "success" or not isinstance(data.get("organicResults"), list):
            raise CloseSpider("discovery_schema_error")
        self.crawler.stats.inc_value("lead_pipeline/searches_completed")
        if not data["organicResults"]:
            self.crawler.stats.inc_value("lead_pipeline/empty_searches")
        for result in data["organicResults"]:
            if len(self.records) >= self.max_businesses:
                break
            try:
                url = business_url(result.get("url", ""))
            except (ValueError, TypeError):
                continue
            if not url or domain_key(url) in self.seen_domains:
                continue
            self.seen_domains.add(domain_key(url))
            host = urlsplit(url).hostname
            self.seeds[host], self.scheduled[host] = url, {url}
            self.seed_hosts.add(host)
            self.pending[host] = 1
            self.records[host] = {"lead_id": lead_id(url), "website": url, "business_name": None,
                "business_name_source_url": None, "contact_name": None, "contact_name_source_url": None,
                "emails": [], "phones": [], "source_urls": [], "_pages": [], "collection_errors": [],
                "target_industry": response.meta["industry"], "target_location": response.meta["location"],
                "discovery_query": response.meta["query"], "discovery_source_url": data.get("url", ""),
                "discovery_title": result.get("title", ""), "discovery_snippet": result.get("snippet", ""),
                "collected_at": datetime.now(timezone.utc).isoformat()}
            yield scrapy.Request(url, callback=self.parse_business, errback=self.business_error,
                meta={"business_host": host, "handle_httpstatus_all": True})

    def search_error(self, failure):
        self.search_failed = True
        raise CloseSpider("discovery_network_error")

    def business_error(self, failure):
        host = failure.request.meta["business_host"]
        self.records[host]["collection_errors"].append({"url": failure.request.url, "error": "network_or_robots_denied"})
        yield from self.complete_page(host)

    def complete_page(self, host):
        self.pending[host] -= 1
        if self.pending[host] == 0:
            row = qualify(self.records[host])
            self.crawler.stats.inc_value("lead_pipeline/businesses_collected")
            yield row

    def parse_business(self, response):
        host = response.meta["business_host"]
        row = self.records[host]
        # Allow only the same registered business domain to handle www migrations.
        if response.status in (301, 302, 303, 307, 308):
            try:
                target = public_url(urljoin(response.url, response.headers.get("Location", b"").decode()))
                if domain_key(target) != domain_key(self.seeds[host]) or response.meta.get("redirect_hops", 0) >= 2:
                    raise ValueError("Untrusted redirect")
                self.seed_hosts.add(urlsplit(target).hostname)
                yield scrapy.Request(target, callback=self.parse_business, errback=self.business_error,
                    meta={"business_host": host, "handle_httpstatus_all": True,
                          "redirect_hops": response.meta.get("redirect_hops", 0)+1}, dont_filter=True)
                return
            except ValueError:
                row["collection_errors"].append({"url": response.url, "error": "redirect_rejected"})
        elif response.status == 200 and isinstance(response, HtmlResponse):
            # Reuse the tested collector's parsing and bounded same-host link discovery.
            # Its link filter uses the seed host, so www redirects are handled explicitly below.
            parsed = list(super().parse(response))
            for entry in parsed:
                if isinstance(entry, scrapy.Request):
                    self.pending[host] += 1
                    yield entry.replace(callback=self.parse_business, errback=self.business_error,
                                        meta={"business_host": host, "handle_httpstatus_all": True})
                    continue
                for field, key in (("emails", "email"), ("phones", "phone")):
                    for contact in entry[field]:
                        existing = next((c for c in row[field] if c[key] == contact[key]), None)
                        if existing:
                            if contact["source_url"] not in existing["source_urls"]:
                                existing["source_urls"].append(contact["source_url"])
                        else:
                            row[field].append({**contact, "source_urls": [contact["source_url"]]})
                if entry["business_name"] and not row["business_name"]:
                    row["business_name"] = entry["business_name"]
                    row["business_name_source_url"] = entry["business_name_source_url"]
            row["source_urls"].append(response.url)
            if not row["contact_name"]:
                for raw in response.css('script[type="application/ld+json"]::text').getall():
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        continue
                    nodes = data if isinstance(data, list) else [data]
                    for node in list(nodes):
                        if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                            nodes.extend(node["@graph"])
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        types = node.get("@type", [])
                        types = [types] if isinstance(types, str) else types
                        role = node.get("jobTitle", "")
                        if (isinstance(types, list) and "Person" in types and isinstance(role, str)
                                and any(word in role.lower() for word in ("owner", "founder", "president", "director"))
                                and isinstance(node.get("name"), str)):
                            row["contact_name"] = node["name"].strip()[:200] or None
                            row["contact_name_source_url"] = response.url
                            break
            text = " ".join(response.xpath("//body//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript)]").getall())
            row["_pages"].append({"url": response.url, "text": text[:100000]})
        else:
            row["collection_errors"].append({"url": response.url, "error": f"http_{response.status}_or_non_html"})
        yield from self.complete_page(host)

    def closed(self, reason):
        self.logger.info("Lead pipeline finished: %s; businesses=%d; reviewed outreach remains disabled", reason, len(self.records))
