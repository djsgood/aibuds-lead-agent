import json
from unittest.mock import patch
from lead_agent.validation import main,plan


def test_validation_dry_run_has_no_side_effects(tmp_path,capsys):
    path=tmp_path/'not-created'
    with patch('requests.Session.request',side_effect=AssertionError('No live calls')):
        assert main(['--output-dir',str(path)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['network_calls']==0 and result['database_writes']==0
    assert not path.exists()


def test_execution_requires_explicit_charge_approval(tmp_path,capsys):
    assert main(['--execute','--output-dir',str(tmp_path/'never')])==2
    assert json.loads(capsys.readouterr().out)['network_calls']==0
    assert not (tmp_path/'never').exists()


def test_missing_credentials_do_not_open_database(tmp_path,monkeypatch,capsys):
    monkeypatch.delenv('ZYTE_API_KEY',raising=False)
    assert main(['--execute','--allow-provider-charges','--output-dir',str(tmp_path/'never')])==2
    assert json.loads(capsys.readouterr().out)['network_calls']==0
    assert not (tmp_path/'never').exists()


def test_plan_keeps_campaign_and_other_providers_disabled():
    result=plan()
    assert result['max_provider_attempts']==6
    assert result['max_estimated_cost_usd']==0.06
    for feature in ('hunter_enabled','model_enabled','sheet_writes','outreach_enabled','deployment_enabled'):
        assert result[feature] is False


def test_bounded_two_industry_execution_uses_at_most_six_attempts(tmp_path,monkeypatch,capsys):
    import base64
    import requests
    from unittest.mock import patch
    monkeypatch.setenv('ZYTE_API_KEY','synthetic-fixture-key')
    calls=[]
    def respond(session,method,url,**kwargs):
        calls.append((method,url))
        payload=kwargs['json']
        if url.endswith('/search'):
            plumbing='plumbing' in payload['query']
            site='plumbfixture.com' if plumbing else 'rooffixture.com'
            result={'status':'success','url':'https://google.com/search',
                    'organicResults':[{'url':'https://'+site+'/','title':'Synthetic Business'}]}
        else:
            requested=payload['url']
            if requested.endswith('robots.txt'):
                text='User-agent: *\nAllow: /'
            else:
                text='<body>Roofing and plumbing New Martinsville. Request a quote.<script type="application/ld+json">{"@type":"LocalBusiness","name":"Synthetic Business","address":{"addressCountry":"US"}}</script></body>'
            result={'statusCode':200,'httpResponseBody':base64.b64encode(text.encode()).decode()}
        response=requests.Response(); response.status_code=200
        response._content=json.dumps(result).encode()
        return response
    with patch('requests.Session.request',new=respond):
        assert main(['--execute','--allow-provider-charges','--output-dir',str(tmp_path)])==0
    output=json.loads(capsys.readouterr().out)
    report=json.loads(__import__('pathlib').Path(output['report']).read_text())
    assert len(calls)==6
    assert report['companies']==2
    assert report['production_ready'] is False
    assert report['metrics']['budgets'][0]['calls']==6
    assert report['metrics']['outbox']=={}
    assert 'synthetic-fixture-key' not in json.dumps(report)
