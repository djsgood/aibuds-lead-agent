from unittest.mock import patch
import pytest
from lead_agent.providers import Zyte, ProviderFailure
from lead_agent.store import Store


def make_provider():
    store=Store(':memory:')
    return Zyte('fixture-key',store,{'max_calls':3,'max_cost':1,'estimated_cost':.03},search_mode='html'),store


def test_html_discovery_extracts_real_links_and_sources_without_inventing_names():
    p,s=make_provider()
    html='''<a href="/url?q=https%3A%2F%2Froofbusiness.com%2F"><h3>Published title</h3></a>
    <a href="https://www.roofbusiness.com/contact"><h3>Duplicate</h3></a>
    <a href="https://plumbingbusiness.com/">Website</a>
    <a href="https://yelp.com/a"><h3>Directory</h3></a>
    <a href="https://www.google.com/search?q=next">Next</a>
    <a href="https://192.168.1.1/"><h3>Private</h3></a>'''
    with patch.object(p,'fetch',return_value=html) as fetch:
        result=p.search('roofing Wheeling WV')
    assert [r['url'] for r in result['results']]==['https://roofbusiness.com/','https://plumbingbusiness.com/']
    assert result['source_url'].startswith('https://www.google.com/search?')
    fetch.assert_called_once_with(result['source_url'])
    assert 'business_name' not in result['results'][0]
    s.close()


@pytest.mark.parametrize('html',['<html>unrelated content</html>', '<p>Verify you are human</p>', '<p>Before you continue to Google</p>'])
def test_unusable_search_pages_fail_instead_of_producing_fake_leads(html):
    p,s=make_provider()
    with patch.object(p,'fetch',return_value=html), pytest.raises(ProviderFailure): p.search('roofing')
    s.close()


def test_genuine_zero_results_is_distinguished_from_parse_failure():
    p,s=make_provider()
    with patch.object(p,'fetch',return_value='<p>Your search did not match any documents.</p>'):
        assert p.search('no matches')['results']==[]
    s.close()


def test_daily_pagination_is_cached_and_resumes_without_skipping_pages():
    p,s=make_provider()
    html='<a href="https://roofbusiness.com/"><h3>Published business</h3></a>'
    with patch('lead_agent.providers.now',return_value='2026-10-21T00:00:00+00:00'), patch.object(p,'fetch',return_value=html) as fetch:
        first=p.search('roofing Wheeling WV')
        assert p.search('roofing Wheeling WV')==first
        fetch.assert_called_once()
    with patch('lead_agent.providers.now',return_value='2026-10-22T00:00:00+00:00'), patch.object(p,'fetch',return_value=html) as fetch:
        second=p.search('roofing Wheeling WV')
        assert 'start=10' in second['source_url']
        fetch.assert_called_once()
    s.close()
