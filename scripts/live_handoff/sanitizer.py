"""Fail-closed defense-in-depth sanitizer for PODCUT Live Handoff status payloads.

Rejects secrets, private paths, OS usernames/hostnames, emails, source code, internal commands,
profile leaks, media/transcript project data, marker injections, and oversized payloads.
Error messages report ONLY violation codes and field paths, never raw sensitive values.
"""

from __future__ import annotations

import os
import platform
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import ALLOWED_NODES, MANAGED_BEGIN_MARKER, MANAGED_END_MARKER

MAX_INPUT_JSON_BYTES = 24_576
MAX_RENDERED_BYTES = 12_288
MAX_RENDERED_LINES = 180

_CONTROL_CHAR_JSON_RE = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_CHAR_RENDERED_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

_MARKER_INJECTION_RE = re.compile(
    r"(?:<<<PODCUT-AUTO-STATUS|PODCUT-AUTO-STATUS:(?:BEGIN|END)|>>>|LIVE_REFRESH_TEST)",
    re.IGNORECASE,
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\bGOCSPX-[0-9A-Za-z_\-]{10,}\b"),
    re.compile(r"\bya29\.[0-9A-Za-z._\-]{10,}\b"),
    re.compile(r"1//[0-9A-Za-z._\-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_\-]?key|access[_\-]?token|refresh[_\-]?token|client[_\-]?secret|"
        r"auth[_\-]?token|token|secret|password|passwd|pwd|secret[_\-]?key|"
        r"private[_\-]?key|credentials?)\s*[:=]\s*[^\s,;]{3,}",
        re.IGNORECASE,
    ),
)

_ABSOLUTE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[\s\"'(\[=;,])[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[A-Za-z0-9._\-]+\\"),
    re.compile(r"\\\\\?\\"),
    re.compile(
        r"%(?:USERPROFILE|LOCALAPPDATA|APPDATA|HOMEDRIVE|HOMEPATH|TEMP|TMP|USERNAME|COMPUTERNAME)%",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s\"'(\[=;,])/(?:Users|home|root|etc|var|tmp|opt|mnt|srv|proc|Volumes|Library|Applications)(?:/|\b)"
    ),
    re.compile(r"(?:^|[\s\"'(\[=;,])/[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+"),
    re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)"),
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

_SOURCE_CODE_OR_CMD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```"),
    re.compile(
        r"(?:^|\s)(?:def\s+[A-Za-z_]\w*\s*\(|class\s+[A-Za-z_]\w*\s*[:(]|"
        r"from\s+[A-Za-z_][\w.]*\s+import\s+[A-Za-z_*]|"
        r"import\s+(?:os|sys|json|re|subprocess|pathlib|urllib|http|socket|requests|httpx|fastapi|pytest|ctypes|hashlib|shutil|tempfile)\b|"
        r"import\s+\{[^}]+\}\s+from\b|"
        r"function\s+[A-Za-z_]\w*\s*\(|export\s+(?:default|const|function|class)\b|"
        r"SELECT\s+.+\s+FROM\s+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\bpowershell(?:\.exe)?\b|\bcmd\.exe\b|\bpythonw?\.exe\b|\b--basetemp\b|"
        r"\b-NoProfile\b|\bcurl\s+|\bwget\s+|\bssh\s+|\bscp\s+|\bpytest\s+-|"
        r"\bpip\s+install\b|\bnpm\s+(?:ci|install|run)\b)",
        re.IGNORECASE,
    ),
)

_PROFILE_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:backend\.env|workspace-owner\.json|PODCUTAI-InstallerDev|"
        r"oauth-client|oauth-token|client_secret\.json)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[\s\"'(\[=;,])\.dev[\\/]profile(?:\b|[\\/])", re.IGNORECASE),
    re.compile(r"\bAppData[\\/](?:Local|Roaming)\b", re.IGNORECASE),
    re.compile(r"\b[\w.\-]+\.dpapi\b", re.IGNORECASE),
)

_MEDIA_OR_PROJECT_DATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b[\w.()\-]+\.(?:mp4|mov|mkv|webm|avi|wav|mp3|m4a|flac|"
        r"srt|vtt|ass|sqlite|sqlite3|db|pt|pth|ckpt|safetensors|onnx|"
        r"zip|7z|tar\.gz|pem|pfx|p12|key)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}"),
    re.compile(r"\bWEBVTT\b"),
)


@dataclass(frozen=True)
class SanitizationViolation:
    """Redacted violation record containing only rule code, path, and static reason."""

    code: str
    field_path: str
    detail: str


class SanitizationError(ValueError):
    """Fail-closed sanitization rejection without leaking sensitive values."""

    def __init__(self, violations: Iterable[SanitizationViolation]) -> None:
        self.violations = tuple(violations)
        summary = "; ".join(f"[{v.code}] {v.field_path}: {v.detail}" for v in self.violations)
        super().__init__(summary)


def _collect_runtime_identities(extra_identities: Iterable[str] = ()) -> tuple[str, ...]:
    """Collect active OS username/hostname in memory only (never logged or persisted)."""
    candidates: list[str] = []
    for env_key in ("USERNAME", "USER", "COMPUTERNAME"):
        val = os.environ.get(env_key)
        if val:
            candidates.append(val)
    node_name = platform.node()
    if node_name:
        candidates.append(node_name)
    try:
        home_name = Path.home().name
        if home_name:
            candidates.append(home_name)
    except RuntimeError:
        pass
    for item in extra_identities:
        if item:
            candidates.append(item)

    unique: list[str] = []
    seen_lower: set[str] = set()
    for raw in candidates:
        cleaned = raw.strip()
        if len(cleaned) < 3:
            continue
        lowered = cleaned.lower()
        if lowered not in seen_lower:
            seen_lower.add(lowered)
            unique.append(cleaned)
    return tuple(unique)


def _matches_identity(text: str, identity: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(identity)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _scan_text(
    text: str,
    field_path: str,
    *,
    is_rendered: bool,
    identities: tuple[str, ...],
) -> list[SanitizationViolation]:
    violations: list[SanitizationViolation] = []

    ctrl_re = _CONTROL_CHAR_RENDERED_RE if is_rendered else _CONTROL_CHAR_JSON_RE
    if ctrl_re.search(text):
        violations.append(
            SanitizationViolation(
                "CONTROL_CHAR_FORBIDDEN",
                field_path,
                "Disallowed control characters detected.",
            )
        )

    if not is_rendered and _MARKER_INJECTION_RE.search(text):
        violations.append(
            SanitizationViolation(
                "MARKER_INJECTION_FORBIDDEN",
                field_path,
                "Reserved managed-section or test markers are forbidden inside payload values.",
            )
        )

    if any(pat.search(text) for pat in _SECRET_PATTERNS):
        violations.append(
            SanitizationViolation(
                "SECRET_API_KEY_OR_TOKEN",
                field_path,
                "Potential API key, OAuth token, password, or private key pattern detected.",
            )
        )

    if any(pat.search(text) for pat in _ABSOLUTE_PATH_PATTERNS):
        violations.append(
            SanitizationViolation(
                "ABSOLUTE_PATH_FORBIDDEN",
                field_path,
                "Absolute filesystem path, environment path variable, or traversal detected.",
            )
        )

    identity_hit = _EMAIL_RE.search(text) is not None
    if not identity_hit:
        skip_node_enum = field_path == "node_label" or field_path.endswith(".node_label")
        for ident in identities:
            if skip_node_enum and ident.upper() in ALLOWED_NODES:
                continue
            if _matches_identity(text, ident):
                identity_hit = True
                break
    if identity_hit:
        violations.append(
            SanitizationViolation(
                "IDENTITY_OR_EMAIL_FORBIDDEN",
                field_path,
                "Personal email address, OS username, or machine hostname detected.",
            )
        )

    if any(pat.search(text) for pat in _SOURCE_CODE_OR_CMD_PATTERNS):
        violations.append(
            SanitizationViolation(
                "SOURCE_CODE_OR_COMMAND_FORBIDDEN",
                field_path,
                "Source code snippet or sensitive internal CLI command detected.",
            )
        )

    if any(pat.search(text) for pat in _PROFILE_LEAK_PATTERNS):
        violations.append(
            SanitizationViolation(
                "PROFILE_OR_PRIVATE_CONFIG_FORBIDDEN",
                field_path,
                "Private user profile, local config, or credential store reference detected.",
            )
        )

    if any(pat.search(text) for pat in _MEDIA_OR_PROJECT_DATA_PATTERNS):
        violations.append(
            SanitizationViolation(
                "MEDIA_OR_PROJECT_DATA_FORBIDDEN",
                field_path,
                "Video, audio, transcript, model artifact, or project database reference detected.",
            )
        )

    return violations


def sanitize_status_payload(
    payload: Any,
    *,
    extra_forbidden_identities: Iterable[str] = (),
) -> None:
    """Recursively scan all string leaves of a validated status payload (fail-closed)."""
    identities = _collect_runtime_identities(extra_forbidden_identities)
    violations: list[SanitizationViolation] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            violations.extend(
                _scan_text(node, path, is_rendered=False, identities=identities)
            )
        elif isinstance(node, dict):
            for key, value in node.items():
                child_path = f"{path}.{key}" if path != "$" else str(key)
                _walk(value, child_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, f"{path}[{idx}]")

    _walk(payload, "$")
    if violations:
        raise SanitizationError(violations)


def sanitize_rendered_text(
    rendered_text: str,
    *,
    extra_forbidden_identities: Iterable[str] = (),
    max_bytes: int = MAX_RENDERED_BYTES,
    max_lines: int = MAX_RENDERED_LINES,
) -> None:
    """Run final post-render boundary, marker integrity, size, and pattern checks (fail-closed)."""
    violations: list[SanitizationViolation] = []
    encoded = rendered_text.encode("utf-8")
    if len(encoded) > max_bytes:
        violations.append(
            SanitizationViolation(
                "PAYLOAD_TOO_LARGE",
                "rendered_text",
                f"Rendered UTF-8 payload exceeds limit of {max_bytes} bytes.",
            )
        )

    line_count = rendered_text.count("\n") + 1
    if line_count > max_lines:
        violations.append(
            SanitizationViolation(
                "PAYLOAD_TOO_LARGE",
                "rendered_text",
                f"Rendered payload exceeds limit of {max_lines} lines.",
            )
        )

    if (
        rendered_text.count(MANAGED_BEGIN_MARKER) != 1
        or rendered_text.count(MANAGED_END_MARKER) != 1
        or not rendered_text.startswith(MANAGED_BEGIN_MARKER + "\n")
        or not rendered_text.endswith("\n" + MANAGED_END_MARKER)
    ):
        violations.append(
            SanitizationViolation(
                "MARKER_BOUNDARY_INVALID",
                "rendered_text",
                "Rendered preview must contain exactly one valid BEGIN/END managed block.",
            )
        )

    if "LIVE_REFRESH_TEST" in rendered_text.upper():
        violations.append(
            SanitizationViolation(
                "MARKER_INJECTION_FORBIDDEN",
                "rendered_text",
                "Reserved LIVE_REFRESH_TEST marker must never be emitted by publisher.",
            )
        )

    identities = _collect_runtime_identities(extra_forbidden_identities)
    # Exclude literal PC-A / PC-B only if active hostname happens to match the logical node label.
    filtered_identities = tuple(
        ident for ident in identities if ident.upper() not in ALLOWED_NODES
    )
    violations.extend(
        _scan_text(
            rendered_text,
            "rendered_text",
            is_rendered=True,
            identities=filtered_identities,
        )
    )

    if violations:
        raise SanitizationError(violations)
