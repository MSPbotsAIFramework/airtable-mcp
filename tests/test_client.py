"""AirtableClient retry/backoff and error mapping (AC9, TC-ATB-016/017), plus
the timeout-handling case called out in the design's test strategy. Retry
delays are monkeypatched down to milliseconds so the suite stays fast while
still exercising the real retry loop (not a mocked one)."""

from __future__ import annotations

import httpx
import pytest
import respx

from airtable_mcp import client as client_module
from airtable_mcp.client import AirtableClient, MAX_RETRIES
from airtable_mcp.errors import AirtableAPIError, map_airtable_error

AIRTABLE = "https://api.airtable.com/v0"


async def test_rate_limited_response_maps_to_retryable_rate_limited_error(monkeypatch):
    monkeypatch.setattr(client_module, "DEFAULT_RETRY_AFTER_SECONDS", 0.01)
    with respx.mock(assert_all_called=True) as m:
        route = m.get(f"{AIRTABLE}/meta/bases").mock(
            return_value=httpx.Response(429, json={"error": {"type": "TOO_MANY_REQUESTS"}})
        )
        airtable = AirtableClient("token")
        with pytest.raises(AirtableAPIError) as excinfo:
            await airtable.list_bases()

    assert excinfo.value.status_code == 429
    # Gives up after a bounded number of attempts -- "retryable" does not mean
    # "retry forever" (SOP SS5: single call must stay under the time budget).
    assert route.call_count == MAX_RETRIES + 1

    mapped = map_airtable_error(excinfo.value)
    assert mapped.code == "rate_limited"
    assert mapped.retryable is True


async def test_server_error_response_maps_to_retryable_upstream_error(monkeypatch):
    monkeypatch.setattr(client_module, "DEFAULT_RETRY_AFTER_SECONDS", 0.01)
    with respx.mock(assert_all_called=True) as m:
        route = m.get(f"{AIRTABLE}/meta/bases").mock(
            return_value=httpx.Response(503, json={"error": {"type": "SERVICE_UNAVAILABLE"}})
        )
        airtable = AirtableClient("token")
        with pytest.raises(AirtableAPIError) as excinfo:
            await airtable.list_bases()

    assert excinfo.value.status_code == 503
    assert route.call_count == MAX_RETRIES + 1

    mapped = map_airtable_error(excinfo.value)
    assert mapped.code == "upstream_error"
    assert mapped.retryable is True


async def test_timeout_maps_to_retryable_upstream_error():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/meta/bases").mock(side_effect=httpx.ReadTimeout("timed out"))
        airtable = AirtableClient("token")
        with pytest.raises(AirtableAPIError) as excinfo:
            await airtable.list_bases()

    mapped = map_airtable_error(excinfo.value)
    assert mapped.code == "upstream_error"
    assert mapped.retryable is True
