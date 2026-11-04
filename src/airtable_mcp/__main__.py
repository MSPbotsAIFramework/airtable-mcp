"""Entry point: run the ASGI app with uvicorn."""

from __future__ import annotations

import uvicorn

from airtable_mcp.server import build_app
from airtable_mcp.settings import settings


def main() -> None:
    uvicorn.run(build_app(), host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
