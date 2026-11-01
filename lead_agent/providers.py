"""Small provider adapters for optional contact enrichment."""
import re
import time
from typing import Protocol

import requests

from lead_agent.store import now


class DiscoveryResearchProvider(Protocol):
    def search(self, query: str) -> dict: ...
    def fetch(self, url: str) -> str: ...


class ContactVerifier(Protocol):
    def contacts(self, domain: str) -> list[dict]: ...
    def verify(self, email: str) -> dict: ...


class ProviderFailure(RuntimeError):
    pass


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
                response = self.session.request(method, self.base + path, timeout=35, allow_redirects=False, **kwargs)
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
                payload = response.json()
            except ValueError:
                raise ProviderFailure(self.name + " invalid JSON") from None
            if not isinstance(payload, dict):
                raise ProviderFailure(self.name + " invalid response object")
            return payload
        raise ProviderFailure(self.name + " retry limit")


class Hunter(HTTPProvider):
    def __init__(self, key, store, budget, session=None):
        super().__init__("hunter", "https://api.hunter.io/v2/", key, store, budget, session)

    def contacts(self, domain):
        data = self.call("domain-search", headers={"X-API-KEY": self.key}, params={"domain": domain, "type": "personal", "limit": 10}).get("data", {})
        if data.get("domain") and data["domain"].lower() != domain.lower():
            raise ProviderFailure("Enrichment domain mismatch")
        from lead_agent.pipeline_utils import published_email
        contacts = []
        for row in data.get("emails", []):
            email = published_email(row.get("value", ""))
            if not email or row.get("type") != "personal" or email.rsplit("@", 1)[-1].lower() != domain.lower():
                continue
            sources = [s["uri"] for s in row.get("sources", []) if isinstance(s.get("uri"), str) and s["uri"].startswith(("http://", "https://"))]
            contacts.append({"email": email, "name": " ".join(v for v in (row.get("first_name"), row.get("last_name")) if isinstance(v, str)).strip() or None,
                            "role": row.get("position"), "source_urls": sources, "source_url": sources[0] if sources else None,
                            "discovery_method": "hunter_domain_search", "publication_status": "provider_reported_published" if sources else "unverified_candidate",
                            "verification_status": "unverified", "retrieved_at": now(), "provider": "hunter"})
        return contacts

    def verify(self, email):
        data = self.call("email-verifier", headers={"X-API-KEY": self.key}, params={"email": email})
        if data.get("pending"):
            return {"verification_status": "pending", "verification_method": "hunter_email_verifier", "checked_at": now()}
        row = data.get("data", {})
        if str(row.get("email", "")).lower() != email.lower():
            raise ProviderFailure("Verification email mismatch")
        from datetime import datetime, timezone
        try:
            verified_date = datetime.fromisoformat((row.get("verification") or {}).get("date", "")).replace(tzinfo=timezone.utc)
            fresh = 0 <= (datetime.now(timezone.utc) - verified_date).days <= 30
        except (ValueError, TypeError):
            fresh = False
        valid = fresh and row.get("status") == "valid" and row.get("smtp_check") is True and row.get("accept_all") is False and not row.get("disposable") and not row.get("webmail") and not row.get("block")
        return {"verification_status": "independently_verified" if valid else ("invalid" if row.get("status") == "invalid" or row.get("disposable") else "risky" if row.get("accept_all") or row.get("webmail") else "unknown"),
                "dns_mx_check": row.get("mx_records"), "smtp_check": row.get("smtp_check"), "disposable": row.get("disposable"),
                "accept_all": row.get("accept_all"), "verification_method": "hunter_email_verifier", "checked_at": now(),
                "provider_verified_at": (row.get("verification") or {}).get("date"), "verification_source_url": "https://hunter.io/api-documentation/v2#email-verifier"}
