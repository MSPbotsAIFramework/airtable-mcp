# PRD-21295 · Code review

## Round 1 · 2026-11-04 · reviewed dev/PRD-21295 d301888 (base main e1ce41c)

Verdict: approved

### Scope

Full `main..dev/PRD-21295` range (new repo, main line A — nothing pre-existing to diff against): 25 files, all new (`pyproject.toml`, `uv.lock`, `Dockerfile`, `.dockerignore`, `.gitignore`, `README.md`, `src/airtable_mcp/**`, `tests/**`, `docs/tickets/PRD-21295.md`, `docs/tickets/PRD-21295.ac.json`).

### Verification performed independently (not just reading the Self-test section)

- `uv run pytest -v` re-run in a fresh worktree checkout of `d301888`: 22 passed, 0 failed — matches the Self-test claim exactly (same test names, same count).
- `uv run mypy src` re-run: `Success: no issues found in 8 source files` — matches.
- `git ls-files | xargs grep -lP '[\x{4e00}-\x{9fff}]'` on tracked files: no match — confirms the English-only claim (the one CJK finding noted in the Self-test section, in the design doc's own "Open questions" tags, was already fixed before this commit).
- `README.md`'s "MSPbots Connector" section diffed byte-for-byte against `vendor-mcp-development/reference/readme-connector-section.md`: identical, correctly placed after the intro and before "## Authentication headers" (SOP §0.5).
- `docs/tickets/PRD-21295.ac.json` validated with `ticket_store.py validate`: "ledger OK: 20 cases".
- `docker build --platform linux/amd64` could not be re-run independently in this review environment (no docker socket permission here); the Self-test section's account of the build (a real Hatchling `readme` packaging failure caught and fixed by removing `README.md` from `.dockerignore`) is specific, internally consistent with the current `.dockerignore` (confirmed it does **not** exclude `README.md`, while `pyproject.toml:5` declares `readme = "README.md"`), and not the kind of detail a fabricated report would include. Trusted on that basis; not independently re-executed.

### Findings

No must-fix findings. Credential isolation, error-envelope mapping, request-scoped state, batch caps, and the destructive-delete contract were all read against the code (not just the design doc) and hold up:

- `middleware.py`: `CredentialMiddleware` is pure ASGI (not `BaseHTTPMiddleware`, which would buffer Streamable HTTP/SSE), reads `X-Airtable-Api-Key` once per request into a `contextvars.ContextVar`, resets it in `finally`. `tests/test_middleware.py::test_concurrent_requests_do_not_cross_tenant_credentials` exercises this under real `asyncio.gather` concurrency with a deliberate `await` between header-read and ContextVar-read — exactly the window a module-level variable would leak through — and passes.
- `client.py`: the module-level `httpx.AsyncClient` is pooling-only (no default `Authorization` header); each `AirtableClient` instance is constructed fresh per tool call from the ContextVar value, never reused across requests.
- `errors.py` / `tools.py`: every tool catches `AirtableAPIError` specifically and returns a structured envelope (`code`/`message`/`retryable`) from a fixed vocabulary — no tool lets an exception escape to the MCP transport. The AC9 status-to-code mapping table (401/403→`unauthorized`, 404→`not_found`, 422→`invalid_argument`, 429→`rate_limited`/retryable, 5xx→`upstream_error`/retryable) matches the tech design's table exactly, including the 599 (timeout/network-error) sentinel falling into the `>= 500` branch.
- Write tools (`create`/`update`/`delete`) reject batches outside 1–10 **before** calling Airtable (`tools.py:169`, `:200`, `:226`); `airtable_delete_records`'s schema has no formula/filter parameter (`tools.py:223`, asserted directly in `tests/test_tools.py::test_delete_records_schema_has_no_filter_parameter`).
- `partialSuccess` on create is surfaced as a non-fatal `warning`, not dropped and not treated as full failure (`tools.py:188-193`, `tests/test_tools.py::test_create_records_surfaces_partial_success_detail`).

Two minor `suggestion`-level items, both under the SOP's "应当" (should, not must) design guidance, and both too small to affect agent tool selection or BA acceptance:

1. [suggestion] `src/airtable_mcp/tools.py:198`, `:224` — the first line of the docstrings for `airtable_update_records` and `airtable_delete_records` runs to ~102 characters, a few characters over the SOP §2 "first line ≤ 100 chars" guideline. Cosmetic only; the full descriptions stay well under the 500-char cap and remain self-explanatory.
2. [suggestion] `src/airtable_mcp/client.py:91-102` (`_log_outcome`) — structured logs (method/path/status/attempt/duration_ms) don't carry a request-id/correlation-id, which SOP §6 recommends for structured logging. Low priority for a stateless single-hop pass-through service with no multi-step request chains to correlate.

### Reuse / scope / tests / REVIEW.md check (per `legs/review.md` R3 and `mcp-track.md` 4.3)

- Reuse: nothing pre-existing to reuse (empty scaffold); the SOP's own §A3 structure (FastMCP + httpx + pydantic-settings + ContextVar) is reused as intended, same shape as the org's other vendor MCPs. No vendor SDK pulled in (`pyairtable` rejected in the tech design's Option analysis with a specific, checkable reason: its client is built around a long-lived per-instance API key, which fights per-request credential isolation).
- Scope: matches the tech design's change list exactly; no file touched outside the listed set.
- Tests: all 20 `ac.json` cases are `auto` except TC-ATB-020 (needs a real Airtable account — not a blocker per the SOP's "no test account" rule); every `auto` case has a named test function as evidence, re-run and confirmed passing above, not just asserted.
- `REVIEW.md` §2 BLOCKER checklist: no schema/migration (this service has no DB), no `mspack publish` or forced pushes, nothing touches a protected/release branch, base (`main`) was read via a forced `git fetch` immediately before this round (confirmed unchanged at `e1ce41c` per the Handoff section) — no stale-base risk.

Status: frozen @ d301888
