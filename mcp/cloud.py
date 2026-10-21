"""Read-only Scrapy Cloud checks; never expose credentials or provider bodies."""
import re
import httpx


ENDPOINT = 'https://app.zyte.com/api/spiders/list.json'


def cloud_key(raw):
    key = str(raw or '').strip()
    if not key:
        raise ValueError('SCRAPY_CLOUD_API_KEY is missing; use the separate Scrapy Cloud account key.')
    if key.startswith(('"', "'")) or key.endswith(('"', "'")):
        raise ValueError('SCRAPY_CLOUD_API_KEY contains surrounding quotes; correct it in private service settings.')
    return key


def cloud_project(raw='882321'):
    if not re.fullmatch(r'[1-9][0-9]*',str(raw)):
        raise ValueError('SCRAPY_CLOUD_PROJECT_ID must be a numeric project ID.')
    return str(raw)


async def inspect_cloud_access(raw_key, project='882321', transport=None):
    project=cloud_project(project)
    key=cloud_key(raw_key)
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=False,transport=transport) as client:
            response=await client.get(ENDPOINT,params={'project':project},auth=(key,''))
    except httpx.HTTPError:
        return {'status':'network_error','project_id':project,'project_access_confirmed':False}
    result={'http_status':response.status_code,'project_id':project,'project_access_confirmed':False}
    try: payload=response.json()
    except ValueError: payload={}
    if not isinstance(payload,dict): payload={}
    message=str(payload.get('message','')).lower()
    if response.status_code in (401,403) and ('authentication failed' in message or response.status_code==401):
        result['status']='authentication_failed'
        result['action']='Replace or correct SCRAPY_CLOUD_API_KEY using the Scrapy Cloud account key; ZYTE_API_KEY is a separate credential.'
    elif response.status_code==403:
        result['status']='access_denied'
        result['action']='Check project membership and key permissions; a proxy denial is also possible.'
    elif response.status_code==429:
        result['status']='rate_limited'
    elif response.status_code==200 and payload.get('status')=='ok' and isinstance(payload.get('spiders'),list):
        result.update(status='ready',project_access_confirmed=True,spiders=payload['spiders'])
    elif response.status_code==200:
        result['status']='response_schema_mismatch'
    else:
        result['status']='endpoint_unavailable'
    return result
