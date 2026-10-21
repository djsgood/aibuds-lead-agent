"""Optional direct Scrapy/Zyte transport; no MCP or OAuth dependency.

Enable explicitly with SCRAPY_SETTINGS_MODULE=lead_agent.direct_settings.
Use only the business_contacts spider for this bounded extraction path.
"""
import os
from aibuds_crawler.settings import *  # noqa: F403

ZYTE_API_KEY = os.environ.get('ZYTE_API_KEY', '')
if not ZYTE_API_KEY:
    raise ValueError('Configure ZYTE_API_KEY privately before enabling direct transport')
DOWNLOAD_HANDLERS = {
    'http':'scrapy_zyte_api.ScrapyZyteAPIDownloadHandler',
    'https':'scrapy_zyte_api.ScrapyZyteAPIDownloadHandler',
}
DOWNLOADER_MIDDLEWARES = {
    'aibuds_crawler.guard.PublicTargetGuard':50,
    'scrapy_zyte_api.ScrapyZyteAPIDownloaderMiddleware':1000,
}
REQUEST_FINGERPRINTER_CLASS = 'scrapy_zyte_api.ScrapyZyteAPIRequestFingerprinter'
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'
ZYTE_API_TRANSPARENT_MODE = True
ZYTE_API_DEFAULT_PARAMS = {'httpResponseBody':True, 'followRedirect':False}
CONCURRENT_REQUESTS = 1
CLOSESPIDER_PAGECOUNT = 3
RETRY_TIMES = 0
# Robots plus at most three pages. SDK retries are explicitly bounded as well.
ZYTE_API_MAX_REQUESTS = 4
from tenacity import AsyncRetrying, stop_after_attempt
ZYTE_API_RETRY_POLICY = AsyncRetrying(stop=stop_after_attempt(1), reraise=True)
