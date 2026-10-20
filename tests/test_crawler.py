import unittest
import scrapy
from scrapy.http import HtmlResponse
from aibuds_crawler.guard import public_url
from aibuds_crawler.spiders.business_contacts import BusinessContactsSpider


class CrawlerTests(unittest.TestCase):
    def page(self, spider, html, url="https://business.com/"):
        request = scrapy.Request(url, meta={"business_host": "business.com"})
        response = HtmlResponse(url, request=request, body=html.encode(), encoding="utf-8")
        return list(spider.parse(response))

    def test_evidence_and_example_filter(self):
        spider = BusinessContactsSpider(urls="https://business.com")
        results = self.page(spider, '''<body><script>hidden@business.com</script>
        owner@business.com <a href="mailto:OWNER@business.com">email</a>
        jamie@example.com <a href="mailto:fake@example.org">sample</a>
        <a href="tel:+1 (304) 555-1234">Call</a><a href="tel:+13045551234">Call</a></body>''')
        row = results[0]
        self.assertEqual(row["emails"], [{"email": "owner@business.com", "source_url": "https://business.com/"}])
        self.assertEqual(len(row["phones"]), 1)
        self.assertIsNone(row["contact_name"])
        self.assertIsNone(row["business_name"])
        self.assertEqual(row["review_status"], "pending_review")

    def test_name_requires_structured_evidence(self):
        s = BusinessContactsSpider(urls="https://business.com")
        row = self.page(s, '<title>Not a verified name</title><script type="application/ld+json">{"@type":"LocalBusiness","name":"Real Roofing"}</script>')[0]
        self.assertEqual(row["business_name"], "Real Roofing")
        self.assertEqual(row["business_name_source_url"], "https://business.com/")

    def test_only_contact_links_and_page_limit(self):
        s = BusinessContactsSpider(urls="https://business.com")
        rows = self.page(s, '<a href="https://other.com/contact">external</a><a href="/blog">Blog</a><a href="/contact">Contact</a><a href="/about">About</a><a href="/team">Team</a>')
        self.assertEqual([r.url for r in rows if isinstance(r, scrapy.Request)], ["https://business.com/contact", "https://business.com/about"])
        self.assertEqual(len(self.page(s, '<a href="/team">Team</a>')), 1)

    def test_limits_and_missing_seed(self):
        for kwargs in [{}, {"urls":"https://business.com", "max_sites":11}, {"urls":"https://business.com", "pages_per_site":4}, {"urls":",".join("https://site%d.com" % i for i in range(11))}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BusinessContactsSpider(**kwargs)

    def test_seed_deduplication(self):
        s = BusinessContactsSpider(urls='["https://business.com", "https://business.com/contact"]')
        self.assertEqual(len(s.seeds), 1)

    def test_reject_private_and_unsafe_seeds(self):
        for url in ["http://business.com", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://host.internal", "https://user:pass@business.com", "https://business.com:8443"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_missing_contacts_stay_empty(self):
        row = self.page(BusinessContactsSpider(urls="https://business.com"), "<body>No contacts</body>")[0]
        self.assertEqual(row["emails"], [])
        self.assertEqual(row["phones"], [])


if __name__ == "__main__":
    unittest.main()
