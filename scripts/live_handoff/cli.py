"""Offline CLI for PODCUT Live Handoff Stage H2A (validation and dry-run only).

Strictly offline: does not import HTTP/socket clients, does not perform OAuth,
and does not modify Google Docs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# Support isolated execution via `python.exe -B -I scripts/live_handoff/cli.py`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.live_handoff.sanitizer import (  # noqa: E402
    MAX_INPUT_JSON_BYTES,
    SanitizationError,
    SanitizationViolation,
    sanitize_rendered_text,
    sanitize_status_payload,
)
from scripts.live_handoff.schema import (  # noqa: E402
    SCHEMA_VERSION,
    RenderResult,
    SchemaValidationError,
    render_canonical_status,
    validate_status_payload,
)

WORKSPACE_MARKER_NAME = "podcut-dev-workspace.json"
DEFAULT_STATUS_REL_PATH = Path("docs") / "LIVE_STATUS.json"


class BoundaryError(RuntimeError):
    """Fail-closed workspace or output-path boundary violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"[{code}] {message}")


def verify_workspace_root(workspace_root: Path) -> Path:
    """Verify the target directory is a valid PODCUT MultiPC workspace."""
    resolved = workspace_root.resolve()
    marker_path = resolved / WORKSPACE_MARKER_NAME
    if not marker_path.is_file():
        raise BoundaryError(
            "WORKSPACE_MARKER_MISSING",
            "podcut-dev-workspace.json is missing from the expected workspace root.",
        )
    try:
        marker_data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError(
            "WORKSPACE_MARKER_INVALID",
            "podcut-dev-workspace.json cannot be parsed as valid JSON.",
        ) from exc
    if (
        not isinstance(marker_data, dict)
        or marker_data.get("schema") != 1
        or marker_data.get("kind") != "podcut-multipc-dev"
    ):
        raise BoundaryError(
            "WORKSPACE_MARKER_INVALID",
            "Workspace marker does not match podcut-multipc-dev schema 1.",
        )
    return resolved


def _assert_inside_dev_profile(target_path: Path, workspace_root: Path) -> Path:
    """Fail closed unless target_path resolves strictly inside <workspace>/.dev/profile/."""
    profile_root = (workspace_root / ".dev" / "profile").resolve()
    resolved_target = target_path.resolve()
    try:
        resolved_target.relative_to(profile_root)
    except ValueError as exc:
        raise BoundaryError(
            "PREVIEW_PATH_OUTSIDE_PROFILE",
            "Optional preview output must stay strictly inside .dev/profile/.",
        ) from exc
    if resolved_target == profile_root:
        raise BoundaryError(
            "PREVIEW_PATH_INVALID",
            "Preview output must target a file inside .dev/profile/, not the profile root itself.",
        )
    # Reject symlinks/reparse points along existing ancestors inside the workspace.
    for ancestor in (resolved_target, *resolved_target.parents):
        if ancestor.exists() and ancestor.is_symlink():
            raise BoundaryError(
                "PREVIEW_PATH_SYMLINK_FORBIDDEN",
                "Preview output path cannot traverse a symbolic link.",
            )
        if ancestor == workspace_root:
            break
    return resolved_target


def load_validate_and_render(
    input_path: Path,
    *,
    extra_forbidden_identities: Iterable[str] = (),
) -> tuple[dict[str, Any], RenderResult]:
    """Read JSON status file, enforce byte limit, validate allowlist schema, sanitize, and render."""
    if not input_path.is_file():
        raise SchemaValidationError(
            "INPUT_NOT_FOUND",
            "input",
            "Status JSON input file was not found.",
        )
    raw_bytes = input_path.read_bytes()
    if len(raw_bytes) > MAX_INPUT_JSON_BYTES:
        raise SanitizationError(
            [
                SanitizationViolation(
                    "PAYLOAD_TOO_LARGE",
                    "input",
                    f"Input JSON exceeds limit of {MAX_INPUT_JSON_BYTES} bytes.",
                )
            ]
        )
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaValidationError(
            "INPUT_ENCODING_INVALID",
            "input",
            "Status JSON file must be valid UTF-8.",
        ) from exc
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(
            "INPUT_JSON_INVALID",
            "input",
            "Status file is not valid JSON.",
        ) from exc

    validated = validate_status_payload(parsed)
    sanitize_status_payload(
        validated,
        extra_forbidden_identities=extra_forbidden_identities,
    )
    rendered = render_canonical_status(validated)
    sanitize_rendered_text(
        rendered.rendered_text,
        extra_forbidden_identities=extra_forbidden_identities,
    )
    return validated, rendered


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="live-handoff",
        description=(
            "PODCUT Live Handoff Stage H2A (100% offline): validate schema, "
            "run fail-closed sanitization, and render canonical Google Docs preview."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate schema and sanitization without printing the preview body.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate, sanitize, compute SHA-256, and print canonical preview (default).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to status JSON file (default: <workspace>/docs/LIVE_STATUS.json).",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=_REPO_ROOT,
        help="PODCUT MultiPC workspace root containing podcut-dev-workspace.json.",
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help="Emit machine-readable JSON verification summary.",
    )
    parser.add_argument(
        "--write-preview",
        type=Path,
        default=None,
        help="Optional preview file path (must resolve inside <workspace>/.dev/profile/).",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    extra_forbidden_identities: Iterable[str] = (),
) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    is_dry_run = not args.validate_only

    try:
        workspace_root = verify_workspace_root(args.workspace_root)
        input_path = (
            args.input.resolve()
            if args.input is not None
            else (workspace_root / DEFAULT_STATUS_REL_PATH).resolve()
        )
        validated, rendered = load_validate_and_render(
            input_path,
            extra_forbidden_identities=extra_forbidden_identities,
        )

        preview_written = False
        if args.write_preview is not None:
            safe_out = _assert_inside_dev_profile(args.write_preview, workspace_root)
            safe_out.parent.mkdir(parents=True, exist_ok=True)
            safe_out.write_text(rendered.rendered_text, encoding="utf-8", newline="\n")
            preview_written = True

    except BoundaryError as exc:
        print(f"ERROR [{exc.code}]: {exc.detail}", file=sys.stderr)
        return 4
    except SchemaValidationError as exc:
        print(f"SCHEMA_REJECTED [{exc.code}] {exc.field_path}: {exc.detail}", file=sys.stderr)
        return 2
    except SanitizationError as exc:
        for violation in exc.violations:
            print(
                f"SANITIZATION_REJECTED [{violation.code}] "
                f"{violation.field_path}: {violation.detail}",
                file=sys.stderr,
            )
        return 3

    summary: dict[str, Any] = {
        "result": "PASS",
        "mode": "dry-run" if is_dry_run else "validate-only",
        "network_mode": "OFFLINE_ONLY",
        "network_requests": 0,
        "google_docs_modified": False,
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": validated["checkpoint_id"],
        "checkpoint_date": validated["checkpoint_date"],
        "node_label": validated["node_label"],
        "content_sha256": rendered.content_sha256,
        "payload_sha256": rendered.payload_sha256,
        "rendered_bytes": rendered.rendered_bytes,
        "rendered_lines": rendered.rendered_lines,
        "utf16_code_units": rendered.utf16_code_units,
        "preview_written": preview_written,
    }

    if args.json_summary:
        if is_dry_run:
            summary["preview"] = rendered.rendered_text
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print("PODCUT LIVE HANDOFF -- STAGE H2A OFFLINE CHECK: PASS")
    print(f"Mode            : {summary['mode']}")
    print("Network Mode    : OFFLINE_ONLY (0 requests, 0 Google Docs mutations)")
    print(f"Checkpoint      : {summary['checkpoint_id']} ({summary['checkpoint_date']}, {summary['node_label']})")
    print(f"Content SHA-256 : {summary['content_sha256']}")
    print(f"Payload SHA-256 : {summary['payload_sha256']}")
    print(
        f"Rendered Size   : {summary['rendered_bytes']} bytes, "
        f"{summary['rendered_lines']} lines, {summary['utf16_code_units']} UTF-16 units"
    )
    if is_dry_run:
        print("")
        print(rendered.rendered_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
