"""Small provider adapters. No guessed contact facts or unbudgeted retries."""
import base64
import hashlib
import re
import time
import requests
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
            if response.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 524) and attempt == 0:
                time.sleep(2)
                continue
            if response.status_code == 451:
                raise ProviderFailure(self.name + " suppression_requested")
            if response.status_code == 202:
                return {"pending": True}
            if response.status_code != 200:
                category = ""
                try:
                    detail = response.json().get("type", "")
                    if isinstance(detail, str) and re.fullmatch(r"/(?:download|request|auth|limits)/[a-z0-9_-]{1,80}", detail):
                        category = " " + detail
                except (ValueError, AttributeError, TypeError):
                    pass
                raise ProviderFailure(f"{self.name} HTTP {response.status_code}{category}")
            if len(response.content) > 4_000_000:
                raise ProviderFailure(self.name + " response exceeds limit")
            try:
                payload=response.json()
                if not isinstance(payload,dict): raise ProviderFailure(self.name+" invalid response object")
                return payload
            except ValueError:
                raise ProviderFailure(self.name + " invalid JSON") from None
        raise ProviderFailure(self.name + " retry limit")


class Zyte(HTTPProvider):
    def __init__(self, key, store, budget, session=None, search_domain="google.com", search_mode="api"):
        super().__init__("zyte", "https://api.zyte.com/v1/", key, store, budget, session)
        self.search_domain = search_domain
        if search_mode not in ("api", "html"): raise ValueError("Unsupported Zyte search mode")
        self.search_mode = search_mode

    def search(self, query):
        if self.search_mode == "html":
            return self.search_html(query)
        result = self.call("search", "POST", auth=(self.key,""), json={"domain":self.search_domain,
            "query":query,"include":["organic"],"maxResults":20,
            "queryParameters":{"style":"generic","geolocation":"US","locale":"en-US"}})
        if result.get("status") != "success" or not isinstance(result.get("organicResults"), list):
            raise ProviderFailure("Zyte search schema mismatch")
        return {"source_url":result.get("url", ""), "results":result["organicResults"], "retrieved_at":now()}

    def search_html(self, query):
        from bs4 import BeautifulSoup
        from urllib.parse import urlencode, urlsplit, parse_qs, urljoin
        from aibuds_crawler.discovery import business_url, domain_key
        if self.search_domain not in ("google.com", "www.google.com"):
            raise ProviderFailure("HTML discovery currently supports Google only")
        if not isinstance(query, str) or not 1 <= len(query) <= 2048:
            raise ValueError("Search query must contain 1–2048 characters")
        from lead_agent.store import Store
        persistent = isinstance(self.store, Store)
        fingerprint = hashlib.sha256(query.encode()).hexdigest()
        cache_key = "html_search:" + now()[:10] + ":" + fingerprint
        cursor_key = "html_search_cursor:" + fingerprint
        cursor = self.store.get_checkpoint(cursor_key) if persistent else None
        if persistent:
            cached = self.store.get_checkpoint(cache_key)
            if cached is not None: return cached
        offset = cursor.get("offset", 0) if isinstance(cursor, dict) else 0
        if type(offset) is not int or offset not in range(0, 100, 10): offset = 0
        params = {"q":query,"num":10,"hl":"en","gl":"us"}
        if offset: params["start"] = offset
        source = "https://www.google.com/search?" + urlencode(params)
        soup = BeautifulSoup(self.fetch(source), "html.parser")
        text = soup.get_text(" ", strip=True).lower()
        if any(signal in text for signal in ("our systems have detected unusual traffic", "verify you are human", "before you continue to google")):
            raise ProviderFailure("Search returned a challenge or consent page; discovery deferred")
        results, seen = [], set()
        for anchor in soup.select("a[href]"):
            heading = anchor.find("h3")
            if heading is None and anchor.get_text(" ",strip=True).lower() != "website":
                continue
            href = urljoin(source, anchor.get("href", ""))
            parsed = urlsplit(href)
            if parsed.hostname in ("google.com", "www.google.com") and parsed.path == "/url":
                params = parse_qs(parsed.query)
                href = (params.get("q") or params.get("url") or [""])[0]
            try:
                website = business_url(href)
                if not website: continue
                domain = domain_key(website)
                if domain in ("googleadservices.com", "doubleclick.net") or domain in seen: continue
            except (ValueError, TypeError):
                continue
            seen.add(domain)
            results.append({"url":website,"title":heading.get_text(" ",strip=True) if heading else "Published website link"})
        if not results and not any(phrase in text for phrase in ("did not match any documents", "no results found")):
            raise ProviderFailure("Search HTML contained no supported business links; parser or provider response needs review")
        result = {"source_url":source,"results":results[:20],"retrieved_at":now()}
        if persistent:
            # Cache and advance together: interrupted jobs cannot silently skip a page.
            with self.store.transaction():
                self.store.checkpoint(cache_key, result)
                self.store.checkpoint(cursor_key, {"offset": (offset+10) % 100 if results else 0})
        return result

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


class Firecrawl(HTTPProvider):
    """Structured search and HTML extraction; bounded by existing call ledger."""
    def __init__(self, key, store, budget, session=None):
        super().__init__("firecrawl", "https://api.firecrawl.dev/v2/", key, store, budget, session)

    def search(self, query):
        payload = self.call("search", "POST", headers={"Authorization": "Bearer " + self.key},
                            json={"query": query, "limit": 20, "sources": ["web"]})
        data = payload.get("data")
        rows = data.get("web") if isinstance(data, dict) else None
        if payload.get("success") is not True or not isinstance(rows, list):
            raise ProviderFailure("Firecrawl search schema mismatch")
        return {"source_url": "https://api.firecrawl.dev/v2/search",
                "results": [{"url": r["url"], "title": r.get("title", "")}
                            for r in rows if isinstance(r, dict) and isinstance(r.get("url"), str)],
                "retrieved_at": now()}

    def fetch(self, url):
        from aibuds_crawler.guard import public_url
        url = public_url(url)
        # Use the remote provider for all requests; do not connect directly to
        # arbitrary candidate hosts from the worker's private network.
        payload = self.call("scrape", "POST", headers={"Authorization": "Bearer " + self.key},
                            json={"url": url, "formats": ["rawHtml"], "onlyMainContent": False})
        data = payload.get("data")
        if payload.get("success") is not True or not isinstance(data, dict):
            raise ProviderFailure("Firecrawl scrape schema mismatch")
        metadata = data.get("metadata") or {}
        if metadata.get("statusCode") != 200:
            raise ProviderFailure(f"Website HTTP {metadata.get('statusCode')}")
        if not isinstance(data.get("rawHtml"), str):
            raise ProviderFailure("Firecrawl HTML or HTTP status unavailable")
        from urllib.parse import urlsplit
        final = metadata.get("sourceURL") or metadata.get("url")
        if not final or urlsplit(final).hostname != urlsplit(url).hostname:
            raise ProviderFailure("Scrape final host not confirmed")
        if url.endswith("/robots.txt"):
            from bs4 import BeautifulSoup
            raw = data["rawHtml"]
            soup = BeautifulSoup(raw, "html.parser")
            text = soup.get_text() if soup.find() else raw
            if not any(line.strip().lower().startswith(("user-agent:", "sitemap:")) for line in text.splitlines()):
                raise ProviderFailure("robots policy format unconfirmed")
            return text
        return data["rawHtml"]
