"""Shared test helpers: drive the real MCP Streamable HTTP transport (not
internal function calls) so tests exercise the same path a real Gateway
request takes -- credential header -> middleware -> tool -> client -> the
mocked Airtable API -> error mapping -> response envelope."""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from airtable_mcp.server import build_app

DEFAULT_HEADERS = {"Accept": "application/json, text/event-stream"}


def new_client() -> TestClient:
    return TestClient(build_app())


def _parse_envelope(response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"no data line in SSE response: {response.text!r}")
    return response.json()


def call_tool(
    client: TestClient,
    name: str,
    arguments: dict[str, Any],
    credential: str | None = "test-credential",
):
    """POST a tools/call request. Returns (http_response, tool_payload) where
    tool_payload is the already-json.loads()'d string every tool returns."""

    headers = dict(DEFAULT_HEADERS)
    if credential is not None:
        headers["X-Airtable-Api-Key"] = credential

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    if response.status_code != 200:
        return response, None

    envelope = _parse_envelope(response)
    result = envelope["result"]
    text = result["content"][0]["text"]
    return response, json.loads(text)


def list_tools(client: TestClient, credential: str | None = "test-credential"):
    headers = dict(DEFAULT_HEADERS)
    if credential is not None:
        headers["X-Airtable-Api-Key"] = credential
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=headers,
    )
    envelope = _parse_envelope(response)
    return response, envelope["result"]["tools"]
