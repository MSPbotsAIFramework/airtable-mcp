# airtable-mcp

MCP server for Airtable: schema discovery and record CRUD over Airtable's
public Web API, for the MSPbots Agent platform.

- Transport: MCP Streamable HTTP (`POST /mcp`), stateless, one credential
  header per request.
- Stack: Python 3.12, FastMCP, httpx.
- License: Apache-2.0. Copyright MSPbots.ai.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Meaning |
|---|---|---|
| `X-Airtable-Api-Key` | Yes | A bearer-style Airtable credential: a Personal Access Token created at [airtable.com/create/tokens](https://airtable.com/create/tokens), or an OAuth access token obtained via [airtable.com/create/oauth](https://airtable.com/create/oauth). Forwarded unchanged as `Authorization: Bearer <value>` to `api.airtable.com`. Needs at minimum `schema.bases:read` and `data.records:read`; add `data.records:write` to use the create/update/delete tools. |

Missing header -> `401` with a structured error body naming the missing
header. No credential is ever read from an environment variable or config
file.

## Tools

| Tool | Description | Read / Write |
|---|---|---|
| `airtable_list_bases` | List the bases the credential can access | Read |
| `airtable_list_tables` | List a base's tables, fields and types | Read |
| `airtable_list_records` | List/filter/sort/paginate records in a table | Read |
| `airtable_get_record` | Fetch a single record by id | Read |
| `airtable_create_records` | Create 1-10 records in one call | Write |
| `airtable_update_records` | Update 1-10 records; only given fields change | Write |
| `airtable_delete_records` | Delete 1-10 records by explicit id (no bulk/filter delete) | Write, destructive |

Out of scope for this version: record comments, webhooks, schema writes
(creating/altering tables or fields), attachment-upload-specific flows,
enterprise/admin scopes, and the Sync API. See
`docs/tickets/PRD-21295.md` for the full design and rationale.

## Running locally

```bash
uv sync
uv run python -m airtable_mcp
# in another shell
curl -X POST http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-Airtable-Api-Key: your_token_here' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Tests

```bash
uv sync
uv run pytest
uv run mypy src
```

## Docker

```bash
docker build --platform linux/amd64 -t airtable-mcp:dev .
docker run --rm -p 8080:8080 airtable-mcp:dev
```
