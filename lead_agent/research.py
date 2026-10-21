"""Research facts and hypotheses; strict sales qualification is separate."""
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
import scrapy
from scrapy.http import HtmlResponse
from aibuds_crawler.spiders.business_contacts import BusinessContactsSpider
from aibuds_crawler.discovery import qualify, domain_key
from aibuds_crawler.guard import public_url
from lead_agent.store import now
from lead_agent.providers import ProviderFailure

RELEVANT_ROLES = ("owner", "founder", "general manager", "operations", "office manager", "marketing", "sales", "president", "ceo", "chief executive")
GENERIC = {"info","hello","contact","support","sales","admin","office","team","service","enquiries","inquiries"}


def relevant_role(role):
    return bool(role) and any(re.search(r"\b"+re.escape(word)+r"\b", role.lower()) for word in RELEVANT_ROLES)


def structured_nodes(soup):
    for tag in soup.select('script[type="application/ld+json"]'):
        try: data = json.loads(tag.get_text())
        except (ValueError, TypeError): continue
        pending = data if isinstance(data,list) else [data]
        for node in list(pending):
            if isinstance(node,dict) and isinstance(node.get("@graph"),list): pending.extend(node["@graph"])
        yield from (node for node in pending if isinstance(node,dict))


def research(candidate, provider, pages_per_site=3):
    website = candidate["website"]
    parser = BusinessContactsSpider(urls=website, pages_per_site=pages_per_site)
    host = urlsplit(website).hostname
    root = "https://"+host
    robots = RobotFileParser()
    try:
        robots.parse(provider.fetch(root+"/robots.txt").splitlines())
    except ProviderFailure as exc:
        # A missing robots file is not a prohibition; auth/network/other failures are.
        if "Website HTTP 404" not in str(exc):
            raise ProviderFailure("robots_unavailable; research deferred") from None
        robots.parse([])
    row = {**candidate,"business_name":None,"business_name_source_url":None,"contact_name":None,
           "emails":[],"phones":[],"source_urls":[],"fit_evidence":[],"_pages":[],"facts":[],
           "hypotheses":[],"unknowns":[],"people":[],"collection_errors":[],"country":None,"employee_count":None,"collected_at":now()}
    queue, visited = [website], set()
    while queue and len(visited) < pages_per_site:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        if not robots.can_fetch("AIBudBotsContactCrawler",url):
            row["collection_errors"].append({"url":url,"error":"robots_denied"})
            continue
        try: html = provider.fetch(url)
        except ProviderFailure as exc:
            row["collection_errors"].append({"url":url,"error":str(exc)})
            continue
        soup = BeautifulSoup(html,"html.parser")
        for node in structured_nodes(soup):
            types = node.get("@type",[]); types = [types] if isinstance(types,str) else types
            if not isinstance(types,list): continue
            if "Person" in types:
                from aibuds_crawler.spiders.business_contacts import published_email
                email = published_email(str(node.get("email","")).removeprefix("mailto:"))
                if isinstance(node.get("name"),str) and relevant_role(node.get("jobTitle", "")):
                    person = {"name":node["name"],"role":node["jobTitle"],"email":email,"source_url":url,"source_urls":[url],"retrieved_at":now()}
                    row["people"].append(person)
            elif any(t in ("Organization","LocalBusiness","Corporation","RoofingContractor","Plumber","Electrician","HVACBusiness","GeneralContractor","HomeAndConstructionBusiness") for t in types):
                if isinstance(node.get("name"),str):
                    row["business_name"],row["business_name_source_url"] = node["name"],url
                address = node.get("address",{})
                country = address.get("addressCountry") if isinstance(address,dict) else None
                if isinstance(country,dict): country = country.get("name")
                if isinstance(country,str): row["country"] = country
                number = node.get("numberOfEmployees")
                if isinstance(number,dict): number = number.get("value")
                if isinstance(number,int) and not isinstance(number,bool): row["employee_count"] = number
        response = HtmlResponse(url, request=scrapy.Request(url,meta={"business_host":host}),body=html.encode(),encoding="utf-8")
        for extracted in parser.parse(response):
            if isinstance(extracted,scrapy.Request):
                if extracted.url not in queue: queue.append(extracted.url)
            else:
                for field,key in (("emails","email"),("phones","phone")):
                    existing = {v[key] for v in row[field]}
                    row[field].extend(v for v in extracted[field] if v[key] not in existing)
        for tag in soup(["script","style","noscript"]): tag.decompose()
        text = soup.get_text(" ",strip=True)[:100000]
        row["_pages"].append({"url":url,"text":text})
        row["source_urls"].append(url)
    row = qualify(row)
    for ev in row["fit_evidence"]:
        row["facts"].append({**ev,"kind":"observed_fact","retrieved_at":now()})
        if ev["term"] in ("request a quote","free estimate","book online","schedule service","contact us"):
            row["hypotheses"].append({"kind":"hypothesis","summary":"Automating intake or follow-up may help this published inquiry workflow; missed leads have not been established.","evidence":ev})
    row["unknowns"] = [field for field in ("country","employee_count") if row[field] is None]
    for email in row["emails"]:
        person = next((p for p in row["people"] if p["email"] == email["email"]),{})
        email.update({"name":person.get("name"),"role":person.get("role"),"publication_status":"publicly_published",
                      "verification_status":"unverified","retrieved_at":now(),"discovery_method":"website_extraction"})
    # Structured person emails are themselves published evidence even without a visible link.
    for person in row["people"]:
        if person["email"] and not any(e["email"] == person["email"] for e in row["emails"]):
            row["emails"].append({**person,"publication_status":"publicly_published","verification_status":"unverified","discovery_method":"website_jsonld"})
    return row


def fresh_verification(contact, at=None):
    """A saved status alone is insufficient at qualification or export time."""
    if contact.get("verification_status") != "independently_verified" or not contact.get("verification_method"):
        return False
    at = at or datetime.now(timezone.utc)
    for field in ("checked_at", "provider_verified_at"):
        try:
            value = datetime.fromisoformat(contact[field])
            # Provider APIs may supply a date; interpret that explicit date as UTC.
            if value.tzinfo is None:
                if field != "provider_verified_at" or len(contact[field]) != 10:
                    return False
                value = value.replace(tzinfo=timezone.utc)
            if not timedelta(0) <= at - value <= timedelta(days=30):
                return False
        except (KeyError, ValueError, TypeError):
            return False
    return True


def strict_qualification(record, employee_min=5, employee_max=50):
    reasons=[]
    country = str(record.get("country") or "").lower()
    identity = bool(record.get("business_name") and record.get("business_name_source_url"))
    if not identity: reasons.append("company_identity_not_supported")
    if country not in ("us","usa","united states","united states of america"): reasons.append("us_location_unconfirmed")
    if not record.get("industry_match"): reasons.append("industry_fit_unconfirmed")
    if not record.get("hypotheses"): reasons.append("no_evidenced_automation_workflow")
    contacts=[e for e in record.get("emails",[]) if e.get("name") and relevant_role(e.get("role"))
              and (e.get("source_urls") or [e.get("source_url")])[0]
              and fresh_verification(e)
              and e["email"].split("@")[0].lower() not in GENERIC]
    if not contacts: reasons.append("no_verified_relevant_decision_maker")
    headcount=record.get("employee_count")
    size_fit = isinstance(headcount,int) and employee_min <= headcount <= employee_max
    scores={"business_fit":20*bool(record.get("industry_match")),"workflow_evidence":20*bool(record.get("hypotheses")),
            "decision_maker":20*bool(contacts),"verification":20*bool(contacts),"identity":15*identity,"commercial_size_preference":5*size_fit}
    record.update({"qualification":"rejected" if reasons else "qualified", "rejection_reasons":reasons,
                   "score_components":scores,"fit_score":sum(scores.values()),"preferred_contact":contacts[0] if contacts else None,
                   "review_status":"pending_review","outreach_approved":False})
    record["contact_name"] = contacts[0].get("name") if contacts else None
    record["contact_role"] = contacts[0].get("role") if contacts else None
    record["fit_reason"] = ("Published inquiry workflow and relevant service match; a named relevant contact passed independent email verification. Automation benefit remains a research hypothesis.") if not reasons else "Rejected: "+", ".join(reasons)
    return record
