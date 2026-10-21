import asyncio
import json
import unittest
import httpx
from cloud import inspect_cloud_access, cloud_key, cloud_project


class CloudTests(unittest.TestCase):
    def check(self, status, body):
        def handler(request):
            self.assertEqual(request.url.params['project'],'882346')
            self.assertIn('Basic ',request.headers['Authorization'])
            return httpx.Response(status,json=body)
        return asyncio.run(inspect_cloud_access('fixture-cloud-key','882346',httpx.MockTransport(handler)))

    def test_exact_live_auth_failure_is_not_project_permission_claim(self):
        result=self.check(403,{'message':'Authentication failed','status':'error'})
        self.assertEqual(result['status'],'authentication_failed')
        self.assertFalse(result['project_access_confirmed'])

    def test_project_denial_is_distinct(self):
        self.assertEqual(self.check(403,{'message':'Project access denied'})['status'],'access_denied')

    def test_success_requires_schema_and_http(self):
        self.assertTrue(self.check(200,{'status':'ok','spiders':['business_contacts']})['project_access_confirmed'])
        self.assertFalse(self.check(200,{'status':'error','message':'Authentication failed'})['project_access_confirmed'])
        self.assertFalse(self.check(200,{'status':'ok','spiders':None})['project_access_confirmed'])

    def test_provider_error_body_is_never_returned(self):
        result=self.check(403,{'message':'Authentication failed','detail':'fixture-cloud-key secret details'})
        self.assertNotIn('fixture-cloud-key',json.dumps(result))
        self.assertNotIn('secret details',json.dumps(result))

    def test_invalid_key_or_project_prevents_request(self):
        for key in ('', '"fixture"', "'fixture'"):
            with self.assertRaises(ValueError): cloud_key(key)
        for project in ('', '../882346','https://other.com'):
            with self.assertRaises(ValueError): cloud_project(project)
        self.assertEqual(cloud_key(' fixture-key\n'),'fixture-key')

    def test_network_failure_is_not_authentication_failure(self):
        def fail(request): raise httpx.ConnectError('secret provider detail')
        result=asyncio.run(inspect_cloud_access('fixture-key',transport=httpx.MockTransport(fail)))
        self.assertEqual(result['status'],'network_error')
        self.assertNotIn('secret provider detail',json.dumps(result))
