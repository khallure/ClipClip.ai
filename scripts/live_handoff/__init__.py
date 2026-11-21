"""Offline status schema, fail-closed sanitizer, and canonical renderer for PODCUT Live Handoff.

Stage H2A scope: 100% offline. No OAuth, no Google API calls, no credential storage,
and no external third-party packages.
"""

from __future__ import annotations

from .sanitizer import (
    MAX_INPUT_JSON_BYTES,
    MAX_RENDERED_BYTES,
    MAX_RENDERED_LINES,
    SanitizationError,
    SanitizationViolation,
    sanitize_rendered_text,
    sanitize_status_payload,
)
from .schema import (
    ALLOWED_AGENTS,
    ALLOWED_GIT_STATES,
    ALLOWED_NODES,
    ALLOWED_STATUSES,
    DOCUMENT_NAME,
    MANAGED_BEGIN_MARKER,
    MANAGED_END_MARKER,
    MANAGED_SECTION_ID,
    SCHEMA_VERSION,
    RenderResult,
    SchemaValidationError,
    render_canonical_status,
    validate_status_payload,
)

__all__ = [
    "ALLOWED_AGENTS",
    "ALLOWED_GIT_STATES",
    "ALLOWED_NODES",
    "ALLOWED_STATUSES",
    "DOCUMENT_NAME",
    "MANAGED_BEGIN_MARKER",
    "MANAGED_END_MARKER",
    "MANAGED_SECTION_ID",
    "MAX_INPUT_JSON_BYTES",
    "MAX_RENDERED_BYTES",
    "MAX_RENDERED_LINES",
    "RenderResult",
    "SCHEMA_VERSION",
    "SanitizationError",
    "SanitizationViolation",
    "SchemaValidationError",
    "render_canonical_status",
    "sanitize_rendered_text",
    "sanitize_status_payload",
    "validate_status_payload",
]
