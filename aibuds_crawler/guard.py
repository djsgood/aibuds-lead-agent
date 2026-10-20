import ipaddress
from urllib.parse import urlsplit, urlunsplit
from scrapy.exceptions import IgnoreRequest


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


class PublicTargetGuard:
    def process_request(self, request, spider):
        try:
            public_url(request.url)
        except ValueError as exc:
            raise IgnoreRequest(str(exc)) from None
        if urlsplit(request.url).hostname not in spider.seed_hosts:
            raise IgnoreRequest("Request is outside the supplied business websites.")
