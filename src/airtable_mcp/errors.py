"""Structured error envelope and the Airtable-status -> our-code mapping.

Every tool returns a string. On failure that string is this JSON envelope
instead of a raised exception, so an Agent can branch on `error.code` and
decide whether to retry (`error.retryable`) without the call being torn
down as a protocol-level error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
]

_RETRYABLE_CODES: frozenset[ErrorCode] = frozenset({"rate_limited", "upstream_error"})


def error_envelope(code: ErrorCode, message: str, retryable: bool | None = None) -> str:
    """Serialize a structured error as the compact JSON string every tool
    returns on failure. `retryable` defaults to the fixed per-code value
    unless explicitly overridden."""

    if retryable is None:
        retryable = code in _RETRYABLE_CODES
    return json.dumps(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class AirtableAPIError(Exception):
    """Raised by client.py when Airtable responds with a non-2xx status.
    Carries enough to map onto the fixed error-code vocabulary in tools.py;
    never carries the credential."""

    def __init__(self, status_code: int, message: str, airtable_type: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.airtable_type = airtable_type


@dataclass(frozen=True)
class MappedError:
    code: ErrorCode
    retryable: bool


def map_airtable_error(exc: AirtableAPIError) -> MappedError:
    """Map an Airtable HTTP status (+ optional error.type body) onto our
    fixed error-code vocabulary (AC9)."""

    status = exc.status_code
    err_type = (exc.airtable_type or "").upper()

    if status in (401,):
        return MappedError("unauthorized", False)
    if status == 403:
        return MappedError("unauthorized", False)
    if status == 404 or err_type == "NOT_FOUND":
        return MappedError("not_found", False)
    if status == 422:
        return MappedError("invalid_argument", False)
    if status == 429:
        return MappedError("rate_limited", True)
    if status >= 500:
        return MappedError("upstream_error", True)
    # Any other 4xx we did not anticipate: treat as invalid_argument rather
    # than inventing a new code outside the fixed vocabulary.
    return MappedError("invalid_argument", False)


def error_envelope_for_airtable_error(exc: AirtableAPIError) -> str:
    mapped = map_airtable_error(exc)
    return error_envelope(mapped.code, exc.message, mapped.retryable)
