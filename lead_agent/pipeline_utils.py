"""Shared URL, discovery, and contact helpers for the pipeline and crawler."""
import hashlib
import ipaddress
import json
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


DIRECTORIES = {"yelp.com", "facebook.com", "instagram.com", "linkedin.com", "angi.com",
               "homeadvisor.com", "thumbtack.com", "bbb.org", "yellowpages.com",
               "google.com", "bing.com", "youtube.com", "indeed.com", "reddit.com"}
ALIASES = {"roofing": ["roofing", "roofer", "roof repair"],
           "plumbing": ["plumbing", "plumber", "drain cleaning"],
           "hvac": ["hvac", "heating", "air conditioning"],
           "electrical": ["electrician", "electrical"],
           "general contracting": ["general contractor", "remodeling", "renovation"]}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net", "test.com"}


def public_url(value):
    p = urlsplit(value.strip())
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme != "https" or not host or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Supply public HTTPS business URLs without credentials or custom ports.")
    if "." not in host or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
        raise ValueError("Local and test domains are not crawl targets.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP address targets are not supported.")
    return urlunsplit(("https", host, p.path or "/", p.query, ""))


def parse_list(value, label):
    values = json.loads(value) if isinstance(value, str) and value.lstrip().startswith("[") else value.split(";") if isinstance(value, str) else value
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and 0 < len(v.strip()) <= 150 for v in values):
        raise ValueError(label + " must be a JSON list or semicolon-separated values.")
    return list(dict.fromkeys(v.strip() for v in values))


def business_url(value):
    p = urlsplit(value)
    if p.scheme == "http":
        value = urlunsplit(("https", p.netloc, p.path, p.query, ""))
    value = public_url(value)
    host = urlsplit(value).hostname
    if any(host == d or host.endswith("." + d) for d in DIRECTORIES):
        return None
    return value


def domain_key(url):
    # Registered-domain grouping merges www and other same-business subdomains.
    import tldextract
    ext = tldextract.TLDExtract(suffix_list_urls=())(url)
    return ext.top_domain_under_public_suffix or urlsplit(url).hostname.removeprefix("www.")


def lead_id(url):
    return hashlib.sha256(domain_key(url).encode()).hexdigest()[:24]


def evidence(text, terms, url):
    for term in terms:
        match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)
        if match:
            return {"term": term, "quote": text[max(0, match.start()-65):match.end()+90].strip(), "source_url": url}
    return None


def qualify(record):
    service, location, inquiry = None, None, None
    city = record["target_location"].split(",")[0].strip()
    terms = ALIASES.get(record["target_industry"].lower(), [record["target_industry"]])
    for page in record.pop("_pages", []):
        service = service or evidence(page["text"], terms, page["url"])
        location = location or evidence(page["text"], [city], page["url"])
        inquiry = inquiry or evidence(page["text"], ["request a quote", "free estimate", "book online", "schedule service", "contact us"], page["url"])
    record["fit_evidence"] = [e for e in (service, location, inquiry) if e]
    record["industry_match"] = bool(service)
    record["location_match"] = bool(location)
    record["email_status"] = "published_not_deliverability_verified" if record["emails"] else "missing"
    record["fit_score"] = 35*bool(service) + 25*bool(location) + 25*bool(record["emails"]) + 10*bool(inquiry) + 5*bool(record["business_name"])
    record["qualification"] = "potential_fit" if service and location and record["emails"] else "needs_review"
    record["review_status"] = "pending_review"
    record["outreach_approved"] = False
    record["fit_reason"] = ("Website mentions the target service and city, and publishes an email. "
                            "Inquiry follow-up is a possible use case; actual need is unconfirmed.") if record["qualification"] == "potential_fit" else "Insufficient website evidence for service, location, or a published email. Review required."
    record["identity_status"] = "candidate_business_website"
    return record


def published_email(value):
    value = unquote(value).strip().lower()
    if not EMAIL.fullmatch(value):
        return None
    domain = value.rsplit("@", 1)[1]
    if any(domain == d or domain.endswith("." + d) for d in EXAMPLE_DOMAINS):
        return None
    if domain.endswith((".invalid", ".test", ".localhost")):
        return None
    return value


def extract_contact_values(visible_text, mailto_hrefs=(), tel_hrefs=()):
    emails = set(EMAIL.findall(visible_text))
    for href in mailto_hrefs:
        emails.update(unquote(href[7:].split("?")[0]).split(","))
    emails = sorted({email for value in emails if (email := published_email(value))})[:50]
    phones = {}
    for href in tel_hrefs:
        value = unquote(href[4:].split("?")[0]).strip()
        digits = re.sub(r"\D", "", value)
        if 7 <= len(digits) <= 15:
            phones.setdefault(digits, value)
    return emails, list(phones.values())


def contact_page_urls(hrefs, page_url, seed_host):
    links = []
    for href in hrefs:
        try:
            url = public_url(urljoin(page_url, href))
        except ValueError:
            continue
        if urlsplit(url).hostname != seed_host:
            continue
        path = urlsplit(url).path.lower()
        if any(word in path for word in ("contact", "about", "team")) and url not in links:
            links.append(url)
    return links
