"""Shared fixtures. `starlette.testclient.TestClient` drives the ASGI app in
its own background thread/event-loop portal, so `client.py`'s module-level
httpx.AsyncClient singleton ends up bound to that portal's loop. Reset it
between tests so a later test's TestClient (a different thread) never
reuses a client bound to an already-torn-down loop."""

from __future__ import annotations

import pytest

from airtable_mcp import client as client_module
from airtable_mcp.middleware import credential_var


@pytest.fixture(autouse=True)
def _reset_module_state():
    yield
    client_module._http_client = None
    credential_var.set(None)
