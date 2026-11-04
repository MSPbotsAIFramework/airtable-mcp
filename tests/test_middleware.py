"""Credential isolation: missing header -> 401 with no outbound call (AC8,
TC-ATB-002), concurrent requests never cross tenant credentials (AC10,
TC-ATB-018), and an invalid credential never appears in logs (AC11,
TC-ATB-019)."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import respx

from airtable_mcp.middleware import CredentialMiddleware, current_credential
from tests._helpers import call_tool, new_client


def test_missing_credential_header_returns_401_without_calling_airtable():
    with respx.mock(assert_all_called=False):  # zero routes -- any outbound call raises
        with new_client() as client:
            resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )

    assert resp.status_code == 401
    body = resp.json()
    assert body == {
        "error": {
            "code": "not_configured",
            "message": "missing required header: X-Airtable-Api-Key",
            "retryable": False,
        }
    }


async def test_concurrent_requests_do_not_cross_tenant_credentials():
    """Direct ASGI-level test of the ContextVar isolation itself (not routed
    through a real tool), so the race is deterministic: the inner app
    deliberately yields control (await asyncio.sleep) between reading the
    header and observing the ContextVar, which is exactly the window where
    a global/module-level variable would leak the other tenant's value."""

    async def echo_credential_app(scope, receive, send):
        await asyncio.sleep(0.01)
        value = current_credential() or ""
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": value.encode("utf-8")})

    middleware = CredentialMiddleware(echo_credential_app, mcp_path="/mcp")

    async def noop_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    class Collector:
        def __init__(self) -> None:
            self.body = b""

        async def __call__(self, message) -> None:
            if message["type"] == "http.response.body":
                self.body += message.get("body", b"")

    scope_a = {"type": "http", "path": "/mcp", "headers": [(b"x-airtable-api-key", b"tenant-a-token")]}
    scope_b = {"type": "http", "path": "/mcp", "headers": [(b"x-airtable-api-key", b"tenant-b-token")]}
    collector_a, collector_b = Collector(), Collector()

    await asyncio.gather(
        middleware(scope_a, noop_receive, collector_a),
        middleware(scope_b, noop_receive, collector_b),
    )

    assert collector_a.body == b"tenant-a-token"
    assert collector_b.body == b"tenant-b-token"


def test_invalid_credential_never_appears_in_logs(caplog):
    secret = "super-secret-token-should-never-leak"
    with caplog.at_level(logging.DEBUG):
        with respx.mock(assert_all_called=True) as m:
            m.get("https://api.airtable.com/v0/meta/bases").mock(
                return_value=httpx.Response(
                    401, json={"error": {"type": "AUTHENTICATION_REQUIRED", "message": "Invalid API key"}}
                )
            )
            with new_client() as client:
                resp, payload = call_tool(client, "airtable_list_bases", {}, credential=secret)

    assert resp.status_code == 200
    assert payload["error"]["code"] == "unauthorized"
    assert secret not in json.dumps(payload)
    assert secret not in caplog.text
    for record in caplog.records:
        assert secret not in record.getMessage()
