from urllib.parse import urlsplit
from scrapy.exceptions import IgnoreRequest
from lead_agent.pipeline_utils import public_url

class PublicTargetGuard:
    def process_request(self, request, spider):
        if (getattr(spider, "name", "") == "lead_pipeline" and request.meta.get("zyte_discovery")
                and request.url == "https://api.zyte.com/v1/search" and request.method == "POST"):
            return
        try:
            public_url(request.url)
        except ValueError as exc:
            raise IgnoreRequest(str(exc)) from None
        if urlsplit(request.url).hostname not in spider.seed_hosts:
            raise IgnoreRequest("Request is outside the supplied business websites.")
