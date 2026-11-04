"""Request-scoped credential isolation (AC8, AC10, AC11).

A pure ASGI middleware (not Starlette's BaseHTTPMiddleware, which buffers
the whole response and breaks the Streamable HTTP / SSE transport). It
reads the per-tenant credential header once per request, stores it in a
ContextVar for the lifetime of that request only, and resets it in a
`finally` so two concurrent requests -- even from different tenants on the
same running instance -- can never observe each other's value. Nothing is
ever stored in a module-level variable or a class attribute.
"""

from __future__ import annotations

import contextvars

from airtable_mcp.errors import error_envelope

CREDENTIAL_HEADER_NAME = "X-Airtable-Api-Key"
_CREDENTIAL_HEADER_BYTES = CREDENTIAL_HEADER_NAME.lower().encode("ascii")

# Request-scoped only. Never read or written outside a single request's
# middleware/tool call chain -- see AC10, AC11, and SOP SS3.3/SS3.4.
credential_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "airtable_credential", default=None
)


def current_credential() -> str | None:
    """What tools.py/client.py call to read this request's credential."""
    return credential_var.get()


class CredentialMiddleware:
    """ASGI middleware mounted in front of the MCP app only. Paths outside
    `mcp_path` (e.g. /health) pass through untouched -- the health probe
    must not require a credential (SOP SS1.1)."""

    def __init__(self, app, mcp_path: str = "/mcp") -> None:
        self.app = app
        self.mcp_path = mcp_path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.mcp_path):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(_CREDENTIAL_HEADER_BYTES)
        credential = raw.decode("latin-1") if raw else None

        if not credential:
            await _respond_missing_header(send)
            return

        token = credential_var.set(credential)
        try:
            await self.app(scope, receive, send)
        finally:
            credential_var.reset(token)


async def _respond_missing_header(send) -> None:
    body = error_envelope(
        "not_configured",
        f"missing required header: {CREDENTIAL_HEADER_NAME}",
        retryable=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
