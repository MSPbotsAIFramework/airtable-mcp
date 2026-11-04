"""Builds the ASGI app: FastMCP's /mcp endpoint + a local-only /health probe,
wrapped by the per-request credential middleware.

Two structural requirements from the SOP, both easy to get wrong silently:

- The MCP sub-app's lifespan must be wired into the *outer* Starlette app's
  `lifespan=` (SS7). Starlette's `Mount()` does not run a mounted sub-app's
  lifespan on its own -- miss this and `/health` returns 200 while every
  `/mcp` request fails with "Task group is not initialized".
- DNS-rebinding protection must stay off (SS7), since this service sits
  behind an internal gateway/proxy and never sees the original Host header.
  The `fastmcp` package (as opposed to the low-level `mcp` SDK's own
  FastMCP) does not pass `security_settings` into
  `StreamableHTTPSessionManager` at all, so `mcp.server.transport_security.
  TransportSecurityMiddleware` falls back to its own default of
  `enable_dns_rebinding_protection=False` -- verified against fastmcp
  2.14.7's `fastmcp/server/http.py` and `mcp/server/transport_security.py`
  (no fastmcp-level knob exists to override this). Self-check per SOP SS1.3:
  a `tools/list` POST must return 200, not 421.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from airtable_mcp import tools
from airtable_mcp.client import close_http_client
from airtable_mcp.middleware import CredentialMiddleware

INSTRUCTIONS = """\
Airtable: bases, tables, and records in a tenant's Airtable account.

Core concepts: a base is a database; each base has tables; each table has
typed fields and records. Most agent tasks start with airtable_list_bases
or airtable_list_tables to discover ids before reading or writing records.

Typical flows:
- Explore: airtable_list_bases -> airtable_list_tables -> airtable_list_records.
- Look up one record you already have the id for: airtable_get_record.
- Add data: airtable_create_records (1-10 records per call).
- Edit data: airtable_update_records (only given fields change).
- Remove data: airtable_delete_records (explicit ids only, no bulk/filter delete).

All tools require the X-Airtable-Api-Key header (a Personal Access Token or
OAuth access token). Record comments, webhooks, and schema writes
(creating/altering tables or fields) are not supported in this version.
"""


async def health(_: Request) -> JSONResponse:
    """Pure local probe -- never calls the Airtable API (SOP SS1.1)."""
    return JSONResponse({"status": "ok"})


def build_mcp() -> FastMCP:
    mcp = FastMCP("airtable-mcp", instructions=INSTRUCTIONS)
    tools.register(mcp)
    return mcp


def build_app() -> ASGIApp:
    mcp = build_mcp()
    # stateless_http=True here (not on the FastMCP() constructor, which this
    # fastmcp version deprecates for this flag) -- SOP SS1.3: must not
    # require every request to land on the instance that saw `initialize`.
    mcp_app = mcp.http_app(path="/mcp", stateless_http=True)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with mcp_app.lifespan(mcp_app):
            try:
                yield
            finally:
                await close_http_client()

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    return CredentialMiddleware(app, mcp_path="/mcp")
