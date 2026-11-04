"""tools/list snapshot + one happy-path test per tool, plus the AC9 error
mapping tests that are easiest expressed per-tool (TC-ATB-001, 003-015).
Every outbound call to Airtable is mocked with respx -- no network, no real
account needed (SOP SS10)."""

from __future__ import annotations

import httpx
import respx

from tests._helpers import call_tool, list_tools, new_client

AIRTABLE = "https://api.airtable.com/v0"


def test_tools_list_snapshot():
    with new_client() as client:
        _, tools = list_tools(client)
    names = {t["name"] for t in tools}
    assert names == {
        "airtable_list_bases",
        "airtable_list_tables",
        "airtable_list_records",
        "airtable_get_record",
        "airtable_create_records",
        "airtable_update_records",
        "airtable_delete_records",
    }
    for t in tools:
        assert t["description"], f"{t['name']} has no description"
        assert len(t["description"]) <= 500, f"{t['name']} description exceeds 500 chars"


def test_list_bases_returns_accessible_bases():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/meta/bases").mock(
            return_value=httpx.Response(
                200,
                json={
                    "bases": [
                        {"id": "appA", "name": "Base A", "permissionLevel": "create"},
                        {"id": "appB", "name": "Base B", "permissionLevel": "read"},
                    ],
                    "offset": None,
                },
            )
        )
        with new_client() as client:
            resp, payload = call_tool(client, "airtable_list_bases", {})

    assert resp.status_code == 200
    assert payload == {
        "bases": [
            {"id": "appA", "name": "Base A", "permissionLevel": "create"},
            {"id": "appB", "name": "Base B", "permissionLevel": "read"},
        ],
        "offset": None,
    }


def test_list_tables_returns_schema_for_known_base():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/meta/bases/appA/tables").mock(
            return_value=httpx.Response(
                200,
                json={
                    "tables": [
                        {
                            "id": "tblX",
                            "name": "Tasks",
                            "primaryFieldId": "fldName",
                            "fields": [
                                {"id": "fldName", "name": "Name", "type": "singleLineText"},
                                {"id": "fldDone", "name": "Done", "type": "checkbox"},
                            ],
                        }
                    ]
                },
            )
        )
        with new_client() as client:
            resp, payload = call_tool(client, "airtable_list_tables", {"baseId": "appA"})

    assert resp.status_code == 200
    assert payload["tables"] == [
        {
            "id": "tblX",
            "name": "Tasks",
            "primaryFieldId": "fldName",
            "fields": [
                {"id": "fldName", "name": "Name", "type": "singleLineText"},
                {"id": "fldDone", "name": "Done", "type": "checkbox"},
            ],
        }
    ]


def test_list_tables_unknown_base_maps_to_not_found():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/meta/bases/appMissing/tables").mock(
            return_value=httpx.Response(
                404, json={"error": {"type": "NOT_FOUND", "message": "Base not found"}}
            )
        )
        with new_client() as client:
            resp, payload = call_tool(client, "airtable_list_tables", {"baseId": "appMissing"})

    assert resp.status_code == 200  # the MCP call succeeds; the *tool result* carries the error
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["retryable"] is False


def test_list_records_returns_records_with_no_offset_when_all_fit_in_one_page():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(
                200,
                json={
                    "records": [
                        {"id": "rec1", "createdTime": "2026-01-01T00:00:00.000Z", "fields": {"Name": "One"}}
                    ],
                    "offset": None,
                },
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client, "airtable_list_records", {"baseId": "appA", "tableIdOrName": "tblX"}
            )

    assert resp.status_code == 200
    assert payload["offset"] is None
    assert len(payload["records"]) == 1
    assert payload["records"][0]["id"] == "rec1"


def test_list_records_offset_pagination_returns_next_page_without_duplicates():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/appA/tblX").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "records": [{"id": "rec1", "createdTime": "t1", "fields": {}}],
                        "offset": "page2cursor",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "records": [{"id": "rec2", "createdTime": "t2", "fields": {}}],
                        "offset": None,
                    },
                ),
            ]
        )
        with new_client() as client:
            _, first = call_tool(
                client, "airtable_list_records", {"baseId": "appA", "tableIdOrName": "tblX", "pageSize": 1}
            )
            assert first["offset"] == "page2cursor"
            _, second = call_tool(
                client,
                "airtable_list_records",
                {"baseId": "appA", "tableIdOrName": "tblX", "pageSize": 1, "offset": first["offset"]},
            )

    first_ids = {r["id"] for r in first["records"]}
    second_ids = {r["id"] for r in second["records"]}
    assert first_ids == {"rec1"}
    assert second_ids == {"rec2"}
    assert not (first_ids & second_ids)
    assert second["offset"] is None


def test_list_records_rejects_page_size_over_100_without_calling_airtable():
    with respx.mock(assert_all_called=False):  # zero routes registered -> any call raises
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_list_records",
                {"baseId": "appA", "tableIdOrName": "tblX", "pageSize": 101},
            )

    assert resp.status_code == 200
    assert payload["error"]["code"] == "invalid_argument"
    assert payload["error"]["retryable"] is False


def test_get_record_returns_fields_for_existing_record():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/appA/tblX/rec1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "rec1", "createdTime": "2026-01-01T00:00:00.000Z", "fields": {"Name": "One"}},
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_get_record",
                {"baseId": "appA", "tableIdOrName": "tblX", "recordId": "rec1"},
            )

    assert resp.status_code == 200
    assert payload == {
        "id": "rec1",
        "createdTime": "2026-01-01T00:00:00.000Z",
        "fields": {"Name": "One"},
    }


def test_get_record_unknown_id_maps_to_not_found():
    with respx.mock(assert_all_called=True) as m:
        m.get(f"{AIRTABLE}/appA/tblX/recMissing").mock(
            return_value=httpx.Response(
                404, json={"error": {"type": "NOT_FOUND", "message": "Record not found"}}
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_get_record",
                {"baseId": "appA", "tableIdOrName": "tblX", "recordId": "recMissing"},
            )

    assert resp.status_code == 200
    assert payload["error"]["code"] == "not_found"


def test_create_records_creates_up_to_ten_records():
    airtable_records = [
        {"id": f"rec{i}", "createdTime": "t", "fields": {"Name": f"Item {i}"}} for i in range(10)
    ]
    with respx.mock(assert_all_called=True) as m:
        m.post(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(200, json={"records": airtable_records})
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_create_records",
                {
                    "baseId": "appA",
                    "tableIdOrName": "tblX",
                    "records": [{"Name": f"Item {i}"} for i in range(10)],
                },
            )

    assert resp.status_code == 200
    assert len(payload["records"]) == 10
    assert "warning" not in payload


def test_create_records_surfaces_partial_success_detail():
    with respx.mock(assert_all_called=True) as m:
        m.post(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(
                200,
                json={
                    "records": [{"id": "rec1", "createdTime": "t", "fields": {"Name": "One"}}],
                    "details": {
                        "message": "partialSuccess",
                        "invalidFields": {"Attachment": "failed to fetch URL"},
                    },
                },
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_create_records",
                {"baseId": "appA", "tableIdOrName": "tblX", "records": [{"Name": "One"}]},
            )

    assert resp.status_code == 200
    assert len(payload["records"]) == 1  # the record that *was* created is not dropped
    assert payload["warning"]["message"] == "partialSuccess"


def test_create_records_rejects_more_than_ten_without_calling_airtable():
    with respx.mock(assert_all_called=False):
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_create_records",
                {
                    "baseId": "appA",
                    "tableIdOrName": "tblX",
                    "records": [{"Name": f"Item {i}"} for i in range(11)],
                },
            )

    assert resp.status_code == 200
    assert payload["error"]["code"] == "invalid_argument"


def test_update_records_only_changes_given_fields():
    with respx.mock(assert_all_called=True) as m:
        m.patch(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(
                200,
                json={
                    "records": [
                        {"id": "rec1", "createdTime": "t", "fields": {"Name": "One", "Done": True}}
                    ]
                },
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_update_records",
                {"baseId": "appA", "tableIdOrName": "tblX", "records": [{"id": "rec1", "fields": {"Done": True}}]},
            )

    assert resp.status_code == 200
    assert payload["records"][0]["fields"] == {"Name": "One", "Done": True}


def test_update_records_unknown_id_in_batch_surfaces_airtable_error():
    with respx.mock(assert_all_called=True) as m:
        m.patch(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(
                422,
                json={"error": {"type": "INVALID_RECORD_ID", "message": "Unknown record id recMissing"}},
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_update_records",
                {
                    "baseId": "appA",
                    "tableIdOrName": "tblX",
                    "records": [{"id": "recMissing", "fields": {"Done": True}}],
                },
            )

    assert resp.status_code == 200
    assert "error" in payload
    assert "records" not in payload  # never report a partial update as fully applied


def test_delete_records_deletes_exact_ids_given():
    with respx.mock(assert_all_called=True) as m:
        m.delete(f"{AIRTABLE}/appA/tblX").mock(
            return_value=httpx.Response(
                200, json={"records": [{"id": "rec1", "deleted": True}, {"id": "rec2", "deleted": True}]}
            )
        )
        with new_client() as client:
            resp, payload = call_tool(
                client,
                "airtable_delete_records",
                {"baseId": "appA", "tableIdOrName": "tblX", "recordIds": ["rec1", "rec2"]},
            )

    assert resp.status_code == 200
    assert payload["records"] == [
        {"id": "rec1", "deleted": True},
        {"id": "rec2", "deleted": True},
    ]


def test_delete_records_schema_has_no_filter_parameter():
    with new_client() as client:
        _, tools = list_tools(client)
    delete_tool = next(t for t in tools if t["name"] == "airtable_delete_records")
    properties = set(delete_tool["inputSchema"]["properties"].keys())
    assert properties == {"baseId", "tableIdOrName", "recordIds"}
    for forbidden in ("filterByFormula", "filter", "query", "where"):
        assert forbidden not in properties
