import base64
from unittest.mock import Mock, patch
import pytest
from lead_agent.mcp_discovery import discover, RunBudget
from lead_agent.store import Store, BudgetExceeded


def test_preview_has_no_side_effects(tmp_path, monkeypatch):
    path = tmp_path / 'leads.sqlite3'
    monkeypatch.setenv('LEAD_AGENT_DB', str(path))
    with patch('lead_agent.mcp_discovery.Store', side_effect=AssertionError('DB opened')):
        result = discover(['roofing'], ['West Virginia'])
    assert result['network_calls'] == 0
    assert not path.exists()


@pytest.mark.parametrize('kwargs', [{'max_api_requests': 0}, {'max_businesses': True}, {'industries': []}])
def test_reject_invalid_targets(kwargs):
    args = dict(industries=['roofing'], locations=['WV'])
    args.update(kwargs)
    with pytest.raises(ValueError): discover(**args)


def test_retry_limit_counts_each_attempt():
    store = Store(':memory:')
    budget = RunBudget(store, 1)
    budget.reserve_call('zyte', 30, 1, .01)
    with pytest.raises(BudgetExceeded): budget.reserve_call('zyte', 30, 1, .01)
    assert budget.calls == 1
    store.close()


def test_discovers_without_urls_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setenv('LEAD_AGENT_DB', str(tmp_path / 'leads.sqlite3'))
    monkeypatch.setenv('ZYTE_API_KEY', 'fixture-not-live')
    def request(method, url, **kwargs):
        response = Mock(status_code=200, content=b'{}')
        if url.endswith('/search'):
            response.json.return_value = {'status': 'success', 'url': 'https://google.com/search?q=roofing',
                                         'organicResults': [{'url': 'https://roofbusiness.com/'}]}
        else:
            body = 'User-agent: *\nAllow: /' if kwargs['json']['url'].endswith('robots.txt') else '<html><p>Roofing services. Request a quote.</p></html>'
            response.json.return_value = {'statusCode': 200, 'httpResponseBody': base64.b64encode(body.encode()).decode()}
        return response
    with patch('requests.Session.request', side_effect=request) as network:
        result = discover(['roofing'], ['WV'], dry_run=False)
        assert result['researched'] == 1
        assert len(result['prospects']) == 1
        assert result['api_requests'] == 3
        assert result['prospects'][0]['outreach_eligible'] is False
        assert result['automatic_handoff'] is False
        assert result['qualified'] == 0
        calls = network.call_count
        again = discover(['roofing'], ['WV'], dry_run=False)
        assert again['status'] == 'no_new_prospects'
        assert network.call_count == calls
