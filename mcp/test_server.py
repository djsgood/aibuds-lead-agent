"""Retained MCP contract, owner protection, and network-free startup."""
import asyncio
import importlib
import os
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastmcp import Client, FastMCP
import httpx

CONFIG = {
    'GITHUB_CLIENT_ID':'fixture-client',
    'GITHUB_CLIENT_SECRET':'fixture-secret',
    'PUBLIC_BASE_URL':'https://example.com',
    'JWT_SIGNING_KEY':'fixture-key-for-offline-tests-only-not-production',
    'ALLOWED_GITHUB_LOGIN':'djsgood',
    'PORT':'8000',
}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.settings=patch.dict(os.environ, CONFIG, clear=True)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.server=importlib.import_module('server')

    def test_only_contact_extraction_tool_is_exposed(self):
        async def check():
            async with Client(self.server.mcp) as client:
                self.assertEqual({tool.name for tool in await client.list_tools()}, {'collect_business_contacts'})
        self.assertIsNotNone(self.server.auth)
        asyncio.run(check())

    def test_owner_restriction_is_preserved(self):
        for token in (None, SimpleNamespace(claims={'login':'other-user'})):
            with patch.object(self.server,'get_access_token',return_value=token):
                with self.assertRaises(ValueError): self.server.owner_only()
        with patch.object(self.server,'get_access_token',return_value=SimpleNamespace(claims={'login':'DJSGOOD'})):
            self.server.owner_only()

    def test_startup_does_not_probe_external_services(self):
        with patch.object(httpx.AsyncClient,'request',side_effect=AssertionError('Unexpected startup request')), patch.object(FastMCP,'run') as run:
            runpy.run_path(self.server.__file__,run_name='__main__')
        run.assert_called_once_with(transport='http',host='0.0.0.0',port=8000)
