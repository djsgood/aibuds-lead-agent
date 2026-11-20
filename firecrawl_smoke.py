import os
from lead_agent.providers import Firecrawl, ProviderFailure
from lead_agent.store import Store
key = os.environ.get("FIRECRAWL_API_KEY")
if not key:
    raise SystemExit("FIRECRAWL_API_KEY is not configured")
class Ledger:
    def __init__(self):
        self.calls = 0
    def reserve_call(self, name, **kwargs):
        if self.calls >= 2:
            raise ProviderFailure("Smoke test request limit reached")
        self.calls += 1
ledger = Ledger()
provider = Firecrawl(key, ledger, {})
try:
    result = provider.search("HVAC plumbing electrical service companies Wheeling West Virginia")
    count = len(result["results"])
    print("Search results:", count)
    if not count:
        raise SystemExit("Search returned no results")
    # Use only the owner's public website for the HTML format check.
    html = provider.fetch("https://aibudbots.ai/")
    print("HTML characters:", len(html))
    if not html.strip():
        raise SystemExit("Empty HTML")
    print("Discovery adapter smoke test passed")
except ProviderFailure as exc:
    raise SystemExit(str(exc)) from None
finally:
    provider.session.close()
    print("Provider request attempts:", ledger.calls)
