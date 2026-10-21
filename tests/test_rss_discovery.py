from unittest.mock import Mock, patch
import pytest
from lead_agent.providers import ProviderFailure
from lead_agent.rss_discovery import BingRSSWithZyte
from lead_agent.store import Store, BudgetExceeded


def provider(body=b'<rss><channel><item><title>Fixture business</title><link>https://roofbusiness.com/</link></item></channel></rss>', status=200):
    zyte = Mock()
    response = Mock(status_code=status)
    response.iter_content.return_value = [body]
    zyte.session.get.return_value = response
    store = Store(':memory:')
    return BingRSSWithZyte(zyte,store), store, response


def test_public_discovery_uses_no_zyte_calls_and_preserves_sources():
    p,s,response = provider()
    result = p.search('roofing WV')
    assert result['results'][0]['url'] == 'https://roofbusiness.com/'
    assert result['source_url'].startswith('https://www.bing.com/search?format=rss&')
    p.zyte.search.assert_not_called()
    assert s.metrics()['budgets'][0]['estimated_cost'] == 0
    response.close.assert_called_once()
    p.fetch('https://roofbusiness.com/')
    p.zyte.fetch.assert_called_once_with('https://roofbusiness.com/')
    s.close()


@pytest.mark.parametrize('body',[b'<html>challenge</html>', b'broken', b'x'*1000001,
    b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>'])
def test_bad_feeds_are_rejected(body):
    p,s,response = provider(body)
    with pytest.raises(ProviderFailure): p.search('roofing WV')
    response.close.assert_called_once()
    s.close()


def test_rate_limit_is_bounded_and_budgeted():
    p,s,response = provider(status=429)
    with patch('lead_agent.rss_discovery.time.sleep'), pytest.raises(ProviderFailure):
        p.search('roofing WV')
    assert p.session.get.call_count == 2
    assert s.metrics()['budgets'][0]['calls'] == 2
    s.close()
