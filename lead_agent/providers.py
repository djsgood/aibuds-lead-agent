"""Small provider adapters. No guessed contact facts or unbudgeted retries."""
import base64
import time
import requests
from lead_agent.store import now


class ProviderFailure(RuntimeError): pass


class HTTPProvider:
    def __init__(self, name, base, key, store, budget, session=None):
        self.name, self.base, self.key = name, base, key
        self.store, self.budget = store, budget
        self.session = session or requests.Session()

    def call(self, path, method="GET", **kwargs):
        if not self.key or self.key == "test-api-key":
            raise ProviderFailure(self.name + " credentials unavailable or dummy; no live claims allowed")
        for attempt in range(2):
            self.store.reserve_call(self.name, **self.budget)
            try:
                response = self.session.request(method, self.base+path, timeout=35, allow_redirects=False, **kwargs)
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise ProviderFailure(self.name + " network failure") from None
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(2)
                continue
            if response.status_code == 451:
                raise ProviderFailure(self.name + " suppression_requested")
            if response.status_code == 202:
                return {"pending": True}
            if response.status_code != 200:
                raise ProviderFailure(f"{self.name} HTTP {response.status_code}")
            if len(response.content) > 4_000_000:
                raise ProviderFailure(self.name + " response exceeds limit")
            try:
                return response.json()
            except ValueError:
                raise ProviderFailure(self.name + " invalid JSON") from None
        raise ProviderFailure(self.name + " retry limit")


class Zyte(HTTPProvider):
    def __init__(self, key, store, budget, session=None, search_domain="google.com"):
        super().__init__("zyte", "https://api.zyte.com/v1/", key, store, budget, session)
        self.search_domain = search_domain

    def search(self, query):
        result = self.call("search", "POST", auth=(self.key,""), json={"domain":self.search_domain,
            "query":query,"include":["organic"],"maxResults":20,
            "queryParameters":{"style":"generic","geolocation":"US","locale":"en-US"}})
        if result.get("status") != "success" or not isinstance(result.get("organicResults"), list):
            raise ProviderFailure("Zyte search schema mismatch")
        return {"source_url":result.get("url", ""), "results":result["organicResults"], "retrieved_at":now()}

    def fetch(self, url):
        from aibuds_crawler.guard import public_url
        url = public_url(url)
        result = self.call("extract", "POST", auth=(self.key,""), json={"url":url,"httpResponseBody":True,"followRedirect":False})
        status = result.get("statusCode")
        if status != 200:
            raise ProviderFailure(f"Website HTTP {status}; supply/crawl its final HTTPS URL")
        try:
            return base64.b64decode(result["httpResponseBody"], validate=True).decode("utf-8", errors="replace")
        except (KeyError, ValueError):
            raise ProviderFailure("Zyte extract schema mismatch") from None


class Hunter(HTTPProvider):
    def __init__(self, key, store, budget, session=None):
        super().__init__("hunter", "https://api.hunter.io/v2/", key, store, budget, session)

    def contacts(self, domain):
        data = self.call("domain-search", headers={"X-API-KEY":self.key}, params={"domain":domain,"type":"personal","limit":10}).get("data", {})
        if data.get("domain") and data["domain"].lower() != domain.lower():
            raise ProviderFailure("Enrichment domain mismatch")
        contacts = []
        for row in data.get("emails", []):
            from aibuds_crawler.spiders.business_contacts import published_email
            email = published_email(row.get("value", ""))
            if not email or row.get("type") != "personal":
                continue
            name = " ".join(v for v in [row.get("first_name"),row.get("last_name")] if isinstance(v,str)).strip()
            sources = [s["uri"] for s in row.get("sources", []) if isinstance(s.get("uri"),str) and s["uri"].startswith(("http://","https://"))]
            contacts.append({"email":email,"name":name or None,"role":row.get("position"),
                "source_urls":sources,"source_url":sources[0] if sources else None,
                "discovery_method":"hunter_domain_search","publication_status":"provider_reported_published" if sources else "unverified_candidate",
                "verification_status":"unverified","retrieved_at":now(),"provider":"hunter"})
        return contacts

    def verify(self, email):
        data = self.call("email-verifier", headers={"X-API-KEY":self.key}, params={"email":email})
        if data.get("pending"):
            return {"verification_status":"pending","verification_method":"hunter_email_verifier","checked_at":now()}
        row = data.get("data", {})
        if str(row.get("email", "")).lower() != email.lower():
            raise ProviderFailure("Verification email mismatch")
        from datetime import datetime, timezone
        try:
            verified_date=datetime.fromisoformat((row.get("verification") or {}).get("date", "")).replace(tzinfo=timezone.utc)
            fresh=0 <= (datetime.now(timezone.utc)-verified_date).days <= 30
        except (ValueError,TypeError): fresh=False
        valid = fresh and row.get("status") == "valid" and row.get("smtp_check") is True and row.get("accept_all") is False and not row.get("disposable") and not row.get("webmail") and not row.get("block")
        return {"verification_status":"independently_verified" if valid else row.get("status", "unknown"),
                "verification_method":"hunter_email_verifier","checked_at":now(),
                "provider_verified_at":(row.get("verification") or {}).get("date"),
                "verification_source_url":"https://hunter.io/api-documentation/v2#email-verifier"}
