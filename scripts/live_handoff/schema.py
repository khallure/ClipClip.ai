"""Strict allowlist schema validator and canonical text renderer for PODCUT-LIVE-STATUS.

This module uses Python 3.12 standard library only and performs no network operations.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0"
DOCUMENT_NAME = "PODCUT-LIVE-STATUS"
MANAGED_SECTION_ID = "PODCUT-AUTO-STATUS"
MANAGED_BEGIN_MARKER = f"<<<{MANAGED_SECTION_ID}:BEGIN>>>"
MANAGED_END_MARKER = f"<<<{MANAGED_SECTION_ID}:END>>>"

ALLOWED_NODES = frozenset({"PC-A", "PC-B"})
ALLOWED_AGENTS = frozenset({"Antigravity", "Codex", "Manual"})
ALLOWED_STATUSES = frozenset(
    {
        "PASS",
        "FAIL",
        "NOT RUN",
        "BLOCKED",
        "UNVERIFIED",
        "NOT VERIFIED",
        "UNKNOWN",
    }
)
ALLOWED_GIT_STATES = frozenset(
    {
        "NOT CONFIGURED",
        "CLEAN",
        "DIRTY",
        "UNKNOWN",
        "NOT VERIFIED",
    }
)

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "document_name",
        "managed_section_id",
        "checkpoint_date",
        "checkpoint_id",
        "node_label",
        "agent",
        "git_status",
        "stages",
        "latest_test_results",
        "open_issues",
        "next_step",
        "recent_checkpoints",
    }
)
GIT_STATUS_KEYS = frozenset({"state", "branch", "local_sha", "remote_sha"})
STAGE_KEYS = frozenset({"stage_id", "title", "status", "summary"})
TEST_RESULT_KEYS = frozenset(
    {
        "suite_id",
        "target",
        "status",
        "passed",
        "failed",
        "skipped",
        "duration_sec",
        "notes",
    }
)
CHECKPOINT_KEYS = frozenset({"date", "checkpoint_id", "node_label", "status", "note"})

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9 ._/+&()\-]{1,64}$")
_SAFE_BRANCH_RE = re.compile(r"^(?:NOT CONFIGURED|UNKNOWN|NOT VERIFIED|[A-Za-z0-9._/\-]{1,64})$")
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REL_TARGET_RE = re.compile(r"^[A-Za-z0-9._/\- ]{1,120}$")
_SINGLE_LINE_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

MAX_STAGES = 20
MAX_TEST_RESULTS = 12
MAX_OPEN_ISSUES = 10
MAX_RECENT_CHECKPOINTS = 6


class SchemaValidationError(ValueError):
    """Fail-closed schema validation error without echoing sensitive raw values."""

    def __init__(self, code: str, field_path: str, message: str) -> None:
        self.code = code
        self.field_path = field_path
        self.detail = message
        super().__init__(f"[{code}] {field_path}: {message}")


@dataclass(frozen=True)
class RenderResult:
    """Deterministic rendered preview and cryptographic integrity metadata."""

    rendered_text: str
    canonical_body: str
    content_sha256: str
    payload_sha256: str
    rendered_bytes: int
    rendered_lines: int
    utf16_code_units: int


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError("SCHEMA_TYPE_ERROR", path, "Expected a JSON object.")
    return value


def _require_exact_keys(obj: dict[str, Any], expected: frozenset[str], path: str) -> None:
    keys = set(obj.keys())
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        raise SchemaValidationError(
            "SCHEMA_MISSING_KEYS",
            path,
            f"Missing required key(s): {', '.join(missing)}.",
        )
    if extra:
        raise SchemaValidationError(
            "SCHEMA_EXTRA_KEYS",
            path,
            f"Disallowed extra key(s): {', '.join(extra)}.",
        )


def _require_str(
    value: Any,
    path: str,
    *,
    min_len: int = 1,
    max_len: int = 200,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError("SCHEMA_TYPE_ERROR", path, "Expected a string.")
    normalized = _norm(value)
    if _SINGLE_LINE_CTRL_RE.search(normalized):
        raise SchemaValidationError(
            "SCHEMA_CONTROL_CHAR",
            path,
            "Control characters and embedded newlines are forbidden in single-line fields.",
        )
    stripped = normalized.strip()
    if stripped != normalized:
        raise SchemaValidationError(
            "SCHEMA_WHITESPACE_ERROR",
            path,
            "Leading or trailing whitespace is not allowed.",
        )
    if len(normalized) < min_len or len(normalized) > max_len:
        raise SchemaValidationError(
            "SCHEMA_LENGTH_ERROR",
            path,
            f"String length must be between {min_len} and {max_len} characters.",
        )
    if pattern is not None and not pattern.fullmatch(normalized):
        raise SchemaValidationError(
            "SCHEMA_FORMAT_ERROR",
            path,
            "String does not match the strict allowlist format.",
        )
    return normalized


def _require_enum(value: Any, allowed: frozenset[str], path: str) -> str:
    text = _require_str(value, path, min_len=1, max_len=64)
    if text not in allowed:
        raise SchemaValidationError(
            "SCHEMA_ENUM_ERROR",
            path,
            f"Value must be one of: {', '.join(sorted(allowed))}.",
        )
    return text


def _require_iso_date(value: Any, path: str) -> str:
    text = _require_str(value, path, min_len=10, max_len=10, pattern=_ISO_DATE_RE)
    try:
        datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise SchemaValidationError(
            "SCHEMA_DATE_INVALID",
            path,
            "Expected a valid calendar ISO date (YYYY-MM-DD).",
        ) from exc
    return text


def _require_sha_or_sentinel(value: Any, path: str) -> str:
    text = _require_str(value, path, min_len=3, max_len=40)
    if text in {"N/A", "UNKNOWN", "NOT VERIFIED"}:
        return text
    if not _HEX_SHA_RE.fullmatch(text):
        raise SchemaValidationError(
            "SCHEMA_SHA_FORMAT_ERROR",
            path,
            "Expected a 7-40 char lowercase hex SHA or N/A / UNKNOWN / NOT VERIFIED.",
        )
    return text


def _require_non_neg_int_or_none(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError(
            "SCHEMA_TYPE_ERROR",
            path,
            "Expected a non-negative integer or null.",
        )
    if value < 0 or value > 100_000:
        raise SchemaValidationError(
            "SCHEMA_RANGE_ERROR",
            path,
            "Integer must be between 0 and 100000.",
        )
    return value


def _require_non_neg_duration_or_none(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError(
            "SCHEMA_TYPE_ERROR",
            path,
            "Expected a non-negative number or null.",
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or numeric > 86_400.0:
        raise SchemaValidationError(
            "SCHEMA_RANGE_ERROR",
            path,
            "Duration must be finite and between 0 and 86400 seconds.",
        )
    return round(numeric, 2)


def _require_relative_target(value: Any, path: str) -> str:
    text = _require_str(value, path, min_len=1, max_len=120, pattern=_REL_TARGET_RE)
    if text.startswith("/") or "\\" in text or ":" in text:
        raise SchemaValidationError(
            "SCHEMA_TARGET_PATH_FORBIDDEN",
            path,
            "Target must be a workspace-relative POSIX path without drive letters.",
        )
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SchemaValidationError(
            "SCHEMA_TARGET_PATH_FORBIDDEN",
            path,
            "Target cannot contain empty segments or dot path traversal.",
        )
    return text


def _validate_git_status(raw: Any) -> dict[str, str]:
    obj = _require_dict(raw, "git_status")
    _require_exact_keys(obj, GIT_STATUS_KEYS, "git_status")
    state = _require_enum(obj["state"], ALLOWED_GIT_STATES, "git_status.state")
    branch = _require_str(
        obj["branch"],
        "git_status.branch",
        min_len=1,
        max_len=64,
        pattern=_SAFE_BRANCH_RE,
    )
    local_sha = _require_sha_or_sentinel(obj["local_sha"], "git_status.local_sha")
    remote_sha = _require_sha_or_sentinel(obj["remote_sha"], "git_status.remote_sha")

    if state == "NOT CONFIGURED":
        if branch != "NOT CONFIGURED" or local_sha != "N/A" or remote_sha != "N/A":
            raise SchemaValidationError(
                "SCHEMA_GIT_INCONSISTENT",
                "git_status",
                "When git_status.state is NOT CONFIGURED, branch must be NOT CONFIGURED and SHAs must be N/A.",
            )
    elif state in {"CLEAN", "DIRTY"}:
        if branch in {"NOT CONFIGURED", "UNKNOWN", "NOT VERIFIED"}:
            raise SchemaValidationError(
                "SCHEMA_GIT_INCONSISTENT",
                "git_status.branch",
                "Active Git states require a concrete branch name.",
            )
        if not _HEX_SHA_RE.fullmatch(local_sha):
            raise SchemaValidationError(
                "SCHEMA_GIT_INCONSISTENT",
                "git_status.local_sha",
                "Active Git states require a concrete lowercase hex local_sha.",
            )

    return {
        "state": state,
        "branch": branch,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
    }


def _validate_stages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_STAGES):
        raise SchemaValidationError(
            "SCHEMA_LIST_ERROR",
            "stages",
            f"Expected a list with 1 to {MAX_STAGES} stage entries.",
        )
    seen_ids: set[str] = set()
    validated: list[dict[str, str]] = []
    for idx, item in enumerate(raw):
        base = f"stages[{idx}]"
        obj = _require_dict(item, base)
        _require_exact_keys(obj, STAGE_KEYS, base)
        stage_id = _require_str(
            obj["stage_id"],
            f"{base}.stage_id",
            min_len=1,
            max_len=40,
            pattern=_SAFE_ID_RE,
        )
        if stage_id in seen_ids:
            raise SchemaValidationError(
                "SCHEMA_DUPLICATE_ID",
                f"{base}.stage_id",
                "Duplicate stage_id is not allowed.",
            )
        seen_ids.add(stage_id)
        validated.append(
            {
                "stage_id": stage_id,
                "title": _require_str(obj["title"], f"{base}.title", min_len=1, max_len=100),
                "status": _require_enum(obj["status"], ALLOWED_STATUSES, f"{base}.status"),
                "summary": _require_str(obj["summary"], f"{base}.summary", min_len=1, max_len=220),
            }
        )
    return validated


def _validate_test_results(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_TEST_RESULTS):
        raise SchemaValidationError(
            "SCHEMA_LIST_ERROR",
            "latest_test_results",
            f"Expected a list with 1 to {MAX_TEST_RESULTS} test result entries.",
        )
    validated: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        base = f"latest_test_results[{idx}]"
        obj = _require_dict(item, base)
        _require_exact_keys(obj, TEST_RESULT_KEYS, base)
        suite_id = _require_str(
            obj["suite_id"],
            f"{base}.suite_id",
            min_len=1,
            max_len=48,
            pattern=_SAFE_ID_RE,
        )
        target = _require_relative_target(obj["target"], f"{base}.target")
        status = _require_enum(obj["status"], ALLOWED_STATUSES, f"{base}.status")
        passed = _require_non_neg_int_or_none(obj["passed"], f"{base}.passed")
        failed = _require_non_neg_int_or_none(obj["failed"], f"{base}.failed")
        skipped = _require_non_neg_int_or_none(obj["skipped"], f"{base}.skipped")
        duration_sec = _require_non_neg_duration_or_none(
            obj["duration_sec"], f"{base}.duration_sec"
        )
        notes = _require_str(obj["notes"], f"{base}.notes", min_len=0, max_len=180)

        if status == "PASS":
            if passed is None or passed < 1 or failed != 0 or skipped is None:
                raise SchemaValidationError(
                    "SCHEMA_TEST_INCONSISTENT",
                    base,
                    "PASS test results require passed >= 1, failed == 0, and non-null skipped count.",
                )
        elif status == "FAIL":
            if failed is None or failed < 1:
                raise SchemaValidationError(
                    "SCHEMA_TEST_INCONSISTENT",
                    base,
                    "FAIL test results require failed >= 1.",
                )
        elif status == "NOT RUN":
            if passed is not None or failed is not None or skipped is not None:
                raise SchemaValidationError(
                    "SCHEMA_TEST_INCONSISTENT",
                    base,
                    "NOT RUN test results must leave passed, failed, and skipped as null.",
                )

        validated.append(
            {
                "suite_id": suite_id,
                "target": target,
                "status": status,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "duration_sec": duration_sec,
                "notes": notes,
            }
        )
    return validated


def _validate_open_issues(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_OPEN_ISSUES:
        raise SchemaValidationError(
            "SCHEMA_LIST_ERROR",
            "open_issues",
            f"Expected a list with 0 to {MAX_OPEN_ISSUES} items.",
        )
    return [
        _require_str(item, f"open_issues[{idx}]", min_len=1, max_len=180)
        for idx, item in enumerate(raw)
    ]


def _validate_recent_checkpoints(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) > MAX_RECENT_CHECKPOINTS:
        raise SchemaValidationError(
            "SCHEMA_LIST_ERROR",
            "recent_checkpoints",
            f"Expected a list with 0 to {MAX_RECENT_CHECKPOINTS} items.",
        )
    validated: list[dict[str, str]] = []
    for idx, item in enumerate(raw):
        base = f"recent_checkpoints[{idx}]"
        obj = _require_dict(item, base)
        _require_exact_keys(obj, CHECKPOINT_KEYS, base)
        validated.append(
            {
                "date": _require_iso_date(obj["date"], f"{base}.date"),
                "checkpoint_id": _require_str(
                    obj["checkpoint_id"],
                    f"{base}.checkpoint_id",
                    min_len=1,
                    max_len=48,
                    pattern=_SAFE_ID_RE,
                ),
                "node_label": _require_enum(
                    obj["node_label"], ALLOWED_NODES, f"{base}.node_label"
                ),
                "status": _require_enum(obj["status"], ALLOWED_STATUSES, f"{base}.status"),
                "note": _require_str(obj["note"], f"{base}.note", min_len=1, max_len=160),
            }
        )
    return validated


def validate_status_payload(raw_payload: Any) -> dict[str, Any]:
    """Validate and normalize a raw parsed JSON object against the strict allowlist schema."""
    obj = _require_dict(raw_payload, "$")
    _require_exact_keys(obj, TOP_LEVEL_KEYS, "$")

    schema_version = _require_str(obj["schema_version"], "schema_version", min_len=1, max_len=16)
    if schema_version != SCHEMA_VERSION:
        raise SchemaValidationError(
            "SCHEMA_VERSION_UNSUPPORTED",
            "schema_version",
            f"Only schema_version '{SCHEMA_VERSION}' is supported.",
        )

    document_name = _require_str(obj["document_name"], "document_name", min_len=1, max_len=64)
    if document_name != DOCUMENT_NAME:
        raise SchemaValidationError(
            "SCHEMA_DOCUMENT_MISMATCH",
            "document_name",
            f"document_name must be '{DOCUMENT_NAME}'.",
        )

    managed_section_id = _require_str(
        obj["managed_section_id"], "managed_section_id", min_len=1, max_len=64
    )
    if managed_section_id != MANAGED_SECTION_ID:
        raise SchemaValidationError(
            "SCHEMA_SECTION_MISMATCH",
            "managed_section_id",
            f"managed_section_id must be '{MANAGED_SECTION_ID}'.",
        )

    return {
        "schema_version": schema_version,
        "document_name": document_name,
        "managed_section_id": managed_section_id,
        "checkpoint_date": _require_iso_date(obj["checkpoint_date"], "checkpoint_date"),
        "checkpoint_id": _require_str(
            obj["checkpoint_id"],
            "checkpoint_id",
            min_len=1,
            max_len=64,
            pattern=_SAFE_ID_RE,
        ),
        "node_label": _require_enum(obj["node_label"], ALLOWED_NODES, "node_label"),
        "agent": _require_enum(obj["agent"], ALLOWED_AGENTS, "agent"),
        "git_status": _validate_git_status(obj["git_status"]),
        "stages": _validate_stages(obj["stages"]),
        "latest_test_results": _validate_test_results(obj["latest_test_results"]),
        "open_issues": _validate_open_issues(obj["open_issues"]),
        "next_step": _require_str(obj["next_step"], "next_step", min_len=1, max_len=220),
        "recent_checkpoints": _validate_recent_checkpoints(obj["recent_checkpoints"]),
    }


def _format_count(value: int | None) -> str:
    return "N/A" if value is None else str(value)


def _format_duration(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}s"


def render_canonical_status(validated_payload: dict[str, Any]) -> RenderResult:
    """Render a validated payload into a deterministic bounded managed-section preview."""
    data = validate_status_payload(validated_payload)
    git = data["git_status"]

    body_lines: list[str] = [
        "PODCUT LIVE HANDOFF STATUS",
        f"Document: {data['document_name']}",
        f"Schema-Version: {data['schema_version']}",
        f"Managed-Section: {data['managed_section_id']}",
        f"Checkpoint-Date: {data['checkpoint_date']}",
        f"Checkpoint-ID: {data['checkpoint_id']}",
        f"Active-Node: {data['node_label']}",
        f"Active-Agent: {data['agent']}",
        (
            f"Git-Status: state={git['state']} | branch={git['branch']} | "
            f"local_sha={git['local_sha']} | remote_sha={git['remote_sha']}"
        ),
        "",
        "[STAGE MATRIX]",
    ]

    for stage in data["stages"]:
        body_lines.append(
            f"- {stage['stage_id']} | {stage['status']} | {stage['title']} -- {stage['summary']}"
        )

    body_lines.extend(["", "[LATEST TEST RESULTS]"])
    for result in data["latest_test_results"]:
        suffix = f" | notes={result['notes']}" if result["notes"] else ""
        body_lines.append(
            f"- {result['suite_id']} | {result['status']} | target={result['target']} | "
            f"passed={_format_count(result['passed'])} failed={_format_count(result['failed'])} "
            f"skipped={_format_count(result['skipped'])} | "
            f"duration={_format_duration(result['duration_sec'])}{suffix}"
        )

    body_lines.extend(["", "[OPEN ISSUES]"])
    if data["open_issues"]:
        for issue in data["open_issues"]:
            body_lines.append(f"- {issue}")
    else:
        body_lines.append("- None")

    body_lines.extend(["", "[NEXT STEP]", f"- {data['next_step']}"])

    body_lines.extend(["", "[RECENT CHECKPOINTS]"])
    if data["recent_checkpoints"]:
        for entry in data["recent_checkpoints"]:
            body_lines.append(
                f"- {entry['date']} | {entry['checkpoint_id']} | {entry['node_label']} | "
                f"{entry['status']} | {entry['note']}"
            )
    else:
        body_lines.append("- None")

    canonical_body = _norm("\n".join(line.rstrip(" ") for line in body_lines))
    content_sha256 = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

    rendered_lines_list = [
        MANAGED_BEGIN_MARKER,
        canonical_body,
        "",
        "[PAYLOAD INTEGRITY]",
        f"Content-SHA256: {content_sha256}",
        MANAGED_END_MARKER,
    ]
    rendered_text = _norm("\n".join(rendered_lines_list))
    rendered_bytes_data = rendered_text.encode("utf-8")
    payload_sha256 = hashlib.sha256(rendered_bytes_data).hexdigest()
    utf16_units = len(rendered_text.encode("utf-16-le")) // 2

    return RenderResult(
        rendered_text=rendered_text,
        canonical_body=canonical_body,
        content_sha256=content_sha256,
        payload_sha256=payload_sha256,
        rendered_bytes=len(rendered_bytes_data),
        rendered_lines=rendered_text.count("\n") + 1,
        utf16_code_units=utf16_units,
    )
