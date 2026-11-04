"""Thin async wrapper around Airtable's public Web API (api.airtable.com).

One module-level httpx.AsyncClient is reused across requests for connection
pooling only -- it never carries a default Authorization header. Every call
attaches the caller's credential explicitly, per request (SOP SS3.1/SS3.4:
no shared auth state). Retries on 429/5xx honor Airtable's Retry-After and
are bounded so a single tool call's total time stays under the SOP's <120s
budget (SS5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from airtable_mcp.errors import AirtableAPIError

# Structured, credential-free observability (AC11, SOP SS6): method, path,
# status and duration only -- never headers, never the Authorization value.
logger = logging.getLogger("airtable_mcp.client")

BASE_URL = "https://api.airtable.com/v0"
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 30.0
# Keeps the whole call (incl. retries) comfortably under the SOP's <120s cap.
MAX_CALL_BUDGET_SECONDS = 110.0
# Airtable's 429 body says "wait 30s before retrying" (api/errors.md).
DEFAULT_RETRY_AFTER_SECONDS = 30.0
MAX_RETRIES = 3
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503})

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=READ_TIMEOUT_SECONDS,
                pool=READ_TIMEOUT_SECONDS,
            ),
        )
    return _http_client


async def close_http_client() -> None:
    """Called from the app lifespan on shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


def _to_airtable_error(response: httpx.Response) -> AirtableAPIError:
    message = f"Airtable responded {response.status_code}"
    airtable_type: str | None = None
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            airtable_type = err.get("type")
            msg = err.get("message")
            if msg:
                message = str(msg)
        elif isinstance(err, str):
            airtable_type = err
            message = err
    except ValueError:
        pass
    return AirtableAPIError(response.status_code, message, airtable_type=airtable_type)


def _log_outcome(method: str, path: str, status: int | str, started: float, attempt: int) -> None:
    # Deliberately no headers, no credential, no response body -- just
    # enough to correlate a request without ever touching sensitive data.
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    logger.info(
        "airtable_request method=%s path=%s status=%s attempt=%d duration_ms=%s",
        method,
        path,
        status,
        attempt,
        duration_ms,
    )


class AirtableClient:
    """One instance per request. Holds the caller's credential in memory
    only for the lifetime of that request's tool call -- never persisted,
    never shared across instances."""

    def __init__(self, credential: str) -> None:
        self._credential = credential

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = get_http_client()
        headers = {"Authorization": f"Bearer {self._credential}"}
        deadline = time.monotonic() + MAX_CALL_BUDGET_SECONDS
        started = time.monotonic()
        attempt = 0

        while True:
            attempt += 1
            try:
                response = await client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
            except httpx.TimeoutException as exc:
                _log_outcome(method, path, "timeout", started, attempt)
                raise AirtableAPIError(599, f"request to Airtable timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                _log_outcome(method, path, "network_error", started, attempt)
                raise AirtableAPIError(599, f"network error calling Airtable: {exc}") from exc

            if response.status_code < 400:
                _log_outcome(method, path, response.status_code, started, attempt)
                if response.content:
                    return response.json()
                return {}

            if response.status_code in _RETRYABLE_STATUSES and attempt <= MAX_RETRIES:
                wait_seconds = _retry_after_seconds(response)
                remaining = deadline - time.monotonic()
                if remaining > wait_seconds:
                    await asyncio.sleep(wait_seconds)
                    continue

            _log_outcome(method, path, response.status_code, started, attempt)
            raise _to_airtable_error(response)

    # -- bases / schema -----------------------------------------------

    async def list_bases(self, offset: str | None = None) -> dict[str, Any]:
        params = {"offset": offset} if offset else None
        return await self.request("GET", "/meta/bases", params=params)

    async def get_base_schema(self, base_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/meta/bases/{base_id}/tables")

    # -- records --------------------------------------------------------

    async def list_records(
        self,
        base_id: str,
        table_id_or_name: str,
        params: list[tuple[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # A list of tuples (not a dict) because Airtable needs repeated/
        # bracketed keys -- fields[]=a&fields[]=b, sort[0][field]=... --
        # which a plain dict of query params cannot express.
        return await self.request("GET", f"/{base_id}/{table_id_or_name}", params=params)

    async def get_record(self, base_id: str, table_id_or_name: str, record_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/{base_id}/{table_id_or_name}/{record_id}")

    async def create_records(
        self, base_id: str, table_id_or_name: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body = {"records": [{"fields": r} for r in records]}
        return await self.request("POST", f"/{base_id}/{table_id_or_name}", json_body=body)

    async def update_records(
        self, base_id: str, table_id_or_name: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body = {"records": records}
        return await self.request("PATCH", f"/{base_id}/{table_id_or_name}", json_body=body)

    async def delete_records(
        self, base_id: str, table_id_or_name: str, record_ids: list[str]
    ) -> dict[str, Any]:
        params = [("records[]", rid) for rid in record_ids]
        return await self.request("DELETE", f"/{base_id}/{table_id_or_name}", params=params)
