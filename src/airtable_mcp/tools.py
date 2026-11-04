"""The 7 v1 tools (AC1-AC7): schema discovery + record CRUD.

Each tool builds its Airtable request from typed arguments, reads the
caller's credential from the request-scoped ContextVar (middleware.py),
calls client.py, and maps the result or error (errors.py) into a compact
JSON string. Tools never raise -- client errors become structured error
envelopes so an Agent can branch on `error.code`.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from airtable_mcp.client import AirtableClient
from airtable_mcp.errors import AirtableAPIError, error_envelope, error_envelope_for_airtable_error
from airtable_mcp.middleware import current_credential

# One shared batch-size constant for every write tool (create/update/delete),
# not hardcoded per tool -- see the design's "Reuse & cleanup" section.
# This is DEV01's own bound, not a documented Airtable limit (see
# docs/tickets/PRD-21295.md "Decisions made by DEV01").
MAX_BATCH_SIZE = 10

# airtable_list_records: lower than Airtable's own default/max of 100 for
# token economy (SOP SS2.5); caller can ask for up to Airtable's ceiling.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _client_or_error() -> tuple[AirtableClient | None, str | None]:
    """Every tool calls this first. Credential presence is already enforced
    by middleware.py's 401 short-circuit before a tool ever runs; this is a
    defensive fallback (e.g. a tool invoked directly in a test without the
    ASGI middleware in front of it), returning the same not_configured
    envelope rather than raising."""

    credential = current_credential()
    if not credential:
        return None, error_envelope(
            "not_configured", "missing required header: X-Airtable-Api-Key", retryable=False
        )
    return AirtableClient(credential), None


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def airtable_list_bases(offset: str | None = None) -> str:
        """List the Airtable bases the caller's credential can access.
        Args: offset - pagination cursor from a previous call's response, or omit for the first page."""
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.list_bases(offset=offset)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        bases = [
            {"id": b.get("id"), "name": b.get("name"), "permissionLevel": b.get("permissionLevel")}
            for b in raw.get("bases", [])
        ]
        return _dumps({"bases": bases, "offset": raw.get("offset")})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def airtable_list_tables(baseId: str) -> str:
        """List a base's tables with their fields.
        Args: baseId - the Airtable base id (from airtable_list_bases)."""
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.get_base_schema(baseId)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        tables = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "primaryFieldId": t.get("primaryFieldId"),
                "fields": [
                    {"id": f.get("id"), "name": f.get("name"), "type": f.get("type")}
                    for f in t.get("fields", [])
                ],
            }
            for t in raw.get("tables", [])
        ]
        return _dumps({"tables": tables})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def airtable_list_records(
        baseId: str,
        tableIdOrName: str,
        view: str | None = None,
        filterByFormula: str | None = None,
        fields: list[str] | None = None,
        maxRecords: int | None = None,
        pageSize: int | None = None,
        sort: list[dict[str, str]] | None = None,
        offset: str | None = None,
    ) -> str:
        """List records in a table, optionally filtered, sorted and paginated.
        Args: baseId, tableIdOrName - identify the table. view - an Airtable view name/id to scope the query.
        filterByFormula - an Airtable formula string. fields - field names to return (omit for all fields).
        maxRecords - stop after this many records total. pageSize - records per page, default 50, max 100.
        sort - list of {"field": str, "direction": "asc"|"desc"}. offset - pagination cursor from a previous response."""
        if pageSize is not None and pageSize > MAX_PAGE_SIZE:
            return error_envelope(
                "invalid_argument", f"pageSize must be <= {MAX_PAGE_SIZE}, got {pageSize}", retryable=False
            )
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None

        query: list[tuple[str, Any]] = []
        if view:
            query.append(("view", view))
        if filterByFormula:
            query.append(("filterByFormula", filterByFormula))
        for f in fields or []:
            query.append(("fields[]", f))
        if maxRecords is not None:
            query.append(("maxRecords", maxRecords))
        query.append(("pageSize", pageSize if pageSize is not None else DEFAULT_PAGE_SIZE))
        for i, s in enumerate(sort or []):
            if "field" in s:
                query.append((f"sort[{i}][field]", s["field"]))
            if "direction" in s:
                query.append((f"sort[{i}][direction]", s["direction"]))
        if offset:
            query.append(("offset", offset))

        try:
            raw = await client.list_records(baseId, tableIdOrName, params=query)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        records = [
            {"id": r.get("id"), "createdTime": r.get("createdTime"), "fields": r.get("fields", {})}
            for r in raw.get("records", [])
        ]
        return _dumps({"records": records, "offset": raw.get("offset")})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def airtable_get_record(baseId: str, tableIdOrName: str, recordId: str) -> str:
        """Fetch a single record by id.
        Args: baseId, tableIdOrName - identify the table. recordId - the record's Airtable id."""
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.get_record(baseId, tableIdOrName, recordId)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        return _dumps({"id": raw.get("id"), "createdTime": raw.get("createdTime"), "fields": raw.get("fields", {})})

    @mcp.tool(annotations={"destructiveHint": False, "idempotentHint": False})
    async def airtable_create_records(baseId: str, tableIdOrName: str, records: list[dict[str, Any]]) -> str:
        """Create 1-10 new records in one call.
        Args: baseId, tableIdOrName - identify the table. records - list of 1-10 field-name -> value objects, one per new record."""
        if not records or len(records) > MAX_BATCH_SIZE:
            return error_envelope(
                "invalid_argument",
                f"records must contain 1-{MAX_BATCH_SIZE} field-sets, got {len(records)}",
                retryable=False,
            )
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.create_records(baseId, tableIdOrName, records)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        created = [
            {"id": r.get("id"), "createdTime": r.get("createdTime"), "fields": r.get("fields", {})}
            for r in raw.get("records", [])
        ]
        result: dict[str, Any] = {"records": created}
        # Airtable's documented partialSuccess shape (e.g. an attachment URL
        # failed to upload): don't drop it, surface it as a non-fatal warning
        # alongside the records that *did* get created (AC5).
        details = raw.get("details")
        if isinstance(details, dict) and details.get("message") == "partialSuccess":
            result["warning"] = details
        return _dumps(result)

    @mcp.tool(annotations={"destructiveHint": True, "idempotentHint": True})
    async def airtable_update_records(baseId: str, tableIdOrName: str, records: list[dict[str, Any]]) -> str:
        """Update 1-10 existing records; only the given fields on each record change, other fields are untouched.
        Args: baseId, tableIdOrName - identify the table. records - list of 1-10 {"id": str, "fields": {...}} objects."""
        if not records or len(records) > MAX_BATCH_SIZE:
            return error_envelope(
                "invalid_argument",
                f"records must contain 1-{MAX_BATCH_SIZE} {{id, fields}} pairs, got {len(records)}",
                retryable=False,
            )
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.update_records(baseId, tableIdOrName, records)
        except AirtableAPIError as exc:
            # If Airtable rejects the batch (e.g. an id it can't find), surface
            # that error rather than guessing which records applied (AC6).
            return error_envelope_for_airtable_error(exc)
        updated = [
            {"id": r.get("id"), "createdTime": r.get("createdTime"), "fields": r.get("fields", {})}
            for r in raw.get("records", [])
        ]
        return _dumps({"records": updated})

    @mcp.tool(annotations={"destructiveHint": True, "idempotentHint": True})
    async def airtable_delete_records(baseId: str, tableIdOrName: str, recordIds: list[str]) -> str:
        """Delete 1-10 records by explicit id. There is no formula/filter-based bulk delete -- only explicit ids.
        Args: baseId, tableIdOrName - identify the table. recordIds - list of 1-10 explicit Airtable record ids to delete."""
        if not recordIds or len(recordIds) > MAX_BATCH_SIZE:
            return error_envelope(
                "invalid_argument",
                f"recordIds must contain 1-{MAX_BATCH_SIZE} explicit ids, got {len(recordIds)}",
                retryable=False,
            )
        client, err = _client_or_error()
        if err:
            return err
        assert client is not None
        try:
            raw = await client.delete_records(baseId, tableIdOrName, recordIds)
        except AirtableAPIError as exc:
            return error_envelope_for_airtable_error(exc)
        deleted = [{"id": r.get("id"), "deleted": bool(r.get("deleted"))} for r in raw.get("records", [])]
        return _dumps({"records": deleted})
