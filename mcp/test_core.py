import asyncio
import base64
import os
import unittest
from unittest.mock import patch
import httpx
from core import validate_url, extract_contacts, fetch_contacts

class Tests(unittest.TestCase):
    def test_rejects_non_public_targets(self):
        for value in ["http://example.com", "https://127.0.0.1", "https://[::1]", "https://metadata.internal", "https://user:pass@example.com", "https://example.com:8443"]:
            with self.subTest(url=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_contacts_have_evidence_and_no_invented_name(self):
        result = extract_contacts('<title>Example</title><script>secret@example.com</script><a href="mailto:Owner@Business.com">Email</a><a href="tel:+13045551234">Call</a>', "https://example.com/")
        self.assertEqual([x["email"] for x in result["emails"]], ["owner@business.com"])
        self.assertIsNone(result["contact_name"])
        self.assertIsNone(result["business_name"])
        self.assertEqual(result["phones"][0]["source_url"], "https://example.com/")

    def test_reserved_demo_addresses_are_never_contacts(self):
        result = extract_contacts('<a href="mailto:jamie@example.com">Sample lead jamie@example.com</a> owner@business.com fake@sub.example.org fake@example.net fake@domain.test fake@domain.invalid', 'https://business.com/')
        self.assertEqual([row["email"] for row in result["emails"]], ["owner@business.com"])
        self.assertIn('jamie@example.com', result["text_excerpt"])

    def test_upstream_auth_and_redirect_policy(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://api.zyte.com/v1/extract")
            self.assertEqual(request.headers["authorization"], "Basic " + base64.b64encode(b"test:").decode())
            self.assertIn(b'"followRedirect":false', request.content)
            return httpx.Response(200, json={"statusCode":200, "httpResponseBody":base64.b64encode(b"hello a@business.com").decode()})
        with patch.dict(os.environ, {"ZYTE_API_KEY":"test"}):
            result = asyncio.run(fetch_contacts("https://example.com", httpx.MockTransport(handler)))
        self.assertEqual(result["emails"][0]["email"], "a@business.com")

    def test_upstream_errors_do_not_leak_response_body(self):
        def handler(request):
            return httpx.Response(401, text="sensitive upstream detail")
        with patch.dict(os.environ, {"ZYTE_API_KEY":"test"}), self.assertRaisesRegex(ValueError, "HTTP 401"):
            asyncio.run(fetch_contacts("https://example.com", httpx.MockTransport(handler)))

if __name__ == "__main__":
    unittest.main()
