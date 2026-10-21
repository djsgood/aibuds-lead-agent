"""Free public Bing RSS discovery with paid Zyte website research kept separate."""
import time
from urllib.parse import urlencode
import requests
from defusedxml import ElementTree
from lead_agent.providers import ProviderFailure
from lead_agent.store import now


class BingRSSWithZyte:
    def __init__(self, zyte, store):
        self.zyte, self.store, self.session = zyte, store, zyte.session

    def search(self, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 2048:
            raise ValueError('Search query must contain 1–2048 characters')
        source = 'https://www.bing.com/search?' + urlencode({'format':'rss','q':query})
        for attempt in range(2):
            self.store.reserve_call('bing_rss', max_calls=36, max_cost=0, estimated_cost=0)
            try:
                response = self.session.get(source, timeout=20, allow_redirects=False, stream=True,
                                            headers={'User-Agent':'AIBudBotsResearch/1.0'})
                try:
                    if response.status_code in (429,500,502,503,504) and attempt == 0:
                        time.sleep(1)
                        continue
                    if response.status_code != 200:
                        raise ProviderFailure(f'Bing RSS HTTP {response.status_code}')
                    body = bytearray()
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > 1_000_000:
                            raise ProviderFailure('Bing RSS response exceeds limit')
                finally:
                    response.close()
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise ProviderFailure('Bing RSS network failure') from None
            try:
                root = ElementTree.fromstring(bytes(body))
                if root.tag != 'rss':
                    raise ValueError('Not RSS')
                results = [{'url':item.findtext('link') or '', 'title':item.findtext('title') or ''}
                           for item in root.findall('./channel/item')[:20]]
            except Exception:
                raise ProviderFailure('Bing RSS invalid feed; no challenge bypass attempted') from None
            return {'source_url':source, 'results':results, 'retrieved_at':now()}
        raise ProviderFailure('Bing RSS retry limit')

    def fetch(self, url):
        return self.zyte.fetch(url)
