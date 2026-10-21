"""Small provider adapters. No guessed contact facts or unbudgeted retries."""
import base64
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit, parse_qs, unquote
from typing import Protocol
from lead_agent.store import now


class DiscoveryResearchProvider(Protocol):
    def search(self, query: str) -> dict: ...
    def fetch(self, url: str) -> str: ...


class ContactVerifier(Protocol):
    def contacts(self, domain: str) -> list[dict]: ...
    def verify(self, email: str) -> dict: ...


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
                payload=response.json()
                if not isinstance(payload,dict): raise ProviderFailure(self.name+" invalid response object")
                return payload
            except ValueError:
                raise ProviderFailure(self.name + " invalid JSON") from None
        raise ProviderFailure(self.name + " retry limit")


class OpenWeb:
    """Non-Zyte web discovery and fetch using public-search HTML parsing."""
    RESULT_PATTERN = re.compile(r"^/l/\?kh=-1&uddg=", re.I)

    def __init__(self, store, budget, session=None, search_url="https://duckduckgo.com/html/"):
        self.name = "open_web"
        self.search_url = search_url
        self.store, self.budget = store, budget
        self.session = session or requests.Session()

    @staticmethod
    def _normalize_result(href):
        if not href:
            return None
        if href.startswith("http://"):
            href = "https://" + href[len("http://"):]
        if href.startswith("https://"):
            return href
        parsed = urlsplit(href)
        if parsed.path == "/l/" and parsed.query:
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target.startswith("http://"):
                target = "https://" + target[len("http://"):]
            return unquote(target) if target.startswith("https://") else None
        if OpenWeb.RESULT_PATTERN.match(href):
            target = parse_qs(urlsplit(href).query).get("uddg", [""])[0]
            target = unquote(target)
            if target.startswith("http://"):
                target = "https://" + target[len("http://"):]
            return target if target.startswith("https://") else None
        return None

    def search(self, query):
        self.store.reserve_call(self.name, **self.budget)
        try:
            response = self.session.request("GET", self.search_url, params={"q": query}, timeout=35, allow_redirects=False)
        except requests.RequestException:
            raise ProviderFailure("open_web network failure") from None
        if response.status_code != 200:
            raise ProviderFailure(f"open_web HTTP {response.status_code}")
        if len(response.content) > 4_000_000:
            raise ProviderFailure("open_web response exceeds limit")
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for anchor in soup.select("a.result__a, h2 a"):
            target = self._normalize_result(anchor.get("href", ""))
            if target:
                results.append({"url": target, "title": (anchor.get_text(" ", strip=True) or "")[:300]})
            if len(results) >= 20:
                break
        if not results:
            # Fallback: any absolute links that are not search-engine internals.
            for anchor in soup.select("a[href]"):
                target = self._normalize_result(anchor.get("href", ""))
                if target:
                    results.append({"url": target, "title": (anchor.get_text(" ", strip=True) or "")[:300]})
                if len(results) >= 20:
                    break
        return {"source_url": response.url, "results": results, "retrieved_at": now()}

    def fetch(self, url):
        from aibuds_crawler.guard import public_url
        target = public_url(url)
        self.store.reserve_call(self.name, **self.budget)
        try:
            response = self.session.request("GET", target, timeout=35, allow_redirects=False, headers={"User-Agent": "AIBudBotsContactCrawler/1.0"})
        except requests.RequestException:
            raise ProviderFailure("open_web fetch network failure") from None
        if response.status_code != 200:
            raise ProviderFailure(f"Website HTTP {response.status_code}; supply/crawl its final HTTPS URL")
        if len(response.content) > 4_000_000:
            raise ProviderFailure("Website response exceeds limit")
        ctype = (response.headers.get("Content-Type", "") or "").lower()
        if ctype and "text" not in ctype and "json" not in ctype and "xml" not in ctype:
            raise ProviderFailure("Website content type unsupported")
        return response.text


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
            if not email or row.get("type") != "personal" or email.rsplit("@",1)[-1].lower() != domain.lower():
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
        return {"verification_status":"independently_verified" if valid else ("invalid" if row.get("status")=="invalid" or row.get("disposable") else "risky" if row.get("accept_all") or row.get("webmail") else "unknown"),
                "dns_mx_check":row.get("mx_records"),"smtp_check":row.get("smtp_check"),
                "disposable":row.get("disposable"),"accept_all":row.get("accept_all"),
                "verification_method":"hunter_email_verifier","checked_at":now(),
                "provider_verified_at":(row.get("verification") or {}).get("date"),
                "verification_source_url":"https://hunter.io/api-documentation/v2#email-verifier"}


class VibeProspecting(HTTPProvider):
    """Optional adapter for external prospecting APIs that return business websites."""
    def __init__(self, key, store, budget, session=None, base="https://api.vibe-prospecting.ai/v1/"):
        super().__init__("vibe_prospecting", base, key, store, budget, session)

    def search(self, query):
        payload = self.call("search", "POST", headers={"Authorization": "Bearer " + self.key}, json={"query": query, "limit": 20})
        rows = payload.get("results", [])
        if not isinstance(rows, list):
            raise ProviderFailure("vibe_prospecting search schema mismatch")
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            website = row.get("website") or row.get("url")
            if isinstance(website, str):
                if website.startswith("http://"):
                    website = "https://" + website[len("http://"):]
                if website.startswith("https://"):
                    results.append({"url": website, "title": str(row.get("company_name") or row.get("title") or "")[:300]})
            if len(results) >= 20:
                break
        return {"source_url": payload.get("source_url") or "", "results": results, "retrieved_at": now()}

    def fetch(self, url):
        # Website crawl remains direct and evidence-based, not provider-inferred.
        return OpenWeb(self.store, self.budget, self.session).fetch(url)
