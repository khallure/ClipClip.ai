"""Offline unit tests for PODCUT Live Handoff Stage H2A.

Runs with Python 3.12 standard library only (.tools/python/python.exe -B -I -m unittest).
Any temporary fixture files are written exclusively inside .dev/profile/temp/.
No network requests, no OAuth flows, and no Google Docs operations are performed.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import socket
import sys
import unicodedata
import unittest
import urllib.request
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.live_handoff import (  # noqa: E402
    ALLOWED_STATUSES,
    MANAGED_BEGIN_MARKER,
    MANAGED_END_MARKER,
    MAX_INPUT_JSON_BYTES,
    MAX_RENDERED_BYTES,
    SanitizationError,
    SchemaValidationError,
    cli,
    render_canonical_status,
    sanitize_rendered_text,
    sanitize_status_payload,
    validate_status_payload,
)


def _make_valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "document_name": "PODCUT-LIVE-STATUS",
        "managed_section_id": "PODCUT-AUTO-STATUS",
        "checkpoint_date": "2026-11-21",
        "checkpoint_id": "Stage 2C.2A",
        "node_label": "PC-B",
        "agent": "Antigravity",
        "git_status": {
            "state": "NOT CONFIGURED",
            "branch": "NOT CONFIGURED",
            "local_sha": "N/A",
            "remote_sha": "N/A",
        },
        "stages": [
            {
                "stage_id": "Stage 2C.2A",
                "title": "Portable Runtime Regression",
                "status": "PASS",
                "summary": "20 PASS, 0 FAIL, 0 SKIP in 2.44s.",
            },
            {
                "stage_id": "Stage 2C.2B",
                "title": "Packaged Runtime Tests",
                "status": "NOT RUN",
                "summary": "Awaiting explicit approval.",
            },
        ],
        "latest_test_results": [
            {
                "suite_id": "Portable Runtime",
                "target": "backend/tests/test_portable_runtime.py",
                "status": "PASS",
                "passed": 20,
                "failed": 0,
                "skipped": 0,
                "duration_sec": 2.44,
                "notes": "Isolated local run.",
            }
        ],
        "open_issues": [
            "Git repository is not initialized yet.",
        ],
        "next_step": "Await approval before next stage.",
        "recent_checkpoints": [
            {
                "date": "2026-11-21",
                "checkpoint_id": "Stage 2C.2A",
                "node_label": "PC-B",
                "status": "PASS",
                "note": "Portable runtime tests verified.",
            }
        ],
    }


class LiveHandoffStageH2ATests(unittest.TestCase):
    """Comprehensive offline verification for schema, sanitization, rendering, and CLI."""

    temp_root: Path

    @classmethod
    def setUpClass(cls) -> None:
        dev_temp = _REPO_ROOT / ".dev" / "profile" / "temp"
        if not dev_temp.is_dir():
            raise RuntimeError("Isolated .dev/profile/temp directory is missing.")
        cls.temp_root = dev_temp / f"unittest-h2a-{uuid.uuid4().hex[:12]}"
        cls.temp_root.mkdir(parents=True, exist_ok=False)

    def _write_temp_json(self, rel_path: str, payload: dict | str) -> Path:
        target = self.temp_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8", newline="\n")
        else:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
        return target

    # --- 1. Valid and Invalid Schema ---

    def test_valid_schema_and_workspace_live_status_json(self) -> None:
        live_status_path = _REPO_ROOT / "docs" / "LIVE_STATUS.json"
        validated, rendered = cli.load_validate_and_render(live_status_path)
        self.assertEqual(validated["schema_version"], "1.0")
        self.assertEqual(validated["node_label"], "PC-B")
        self.assertEqual(validated["git_status"]["state"], "NOT CONFIGURED")
        stage_map = {s["stage_id"]: s["status"] for s in validated["stages"]}
        self.assertEqual(stage_map["Stage 2C.2A"], "PASS")
        self.assertEqual(stage_map["Stage 2C.2B"], "NOT RUN")
        self.assertTrue(rendered.rendered_text.startswith(MANAGED_BEGIN_MARKER + "\n"))
        self.assertTrue(rendered.rendered_text.endswith("\n" + MANAGED_END_MARKER))

    def test_schema_rejects_missing_and_extra_keys(self) -> None:
        missing = _make_valid_payload()
        del missing["next_step"]
        with self.assertRaises(SchemaValidationError) as ctx_missing:
            validate_status_payload(missing)
        self.assertEqual(ctx_missing.exception.code, "SCHEMA_MISSING_KEYS")

        extra = _make_valid_payload()
        extra["secret_notes"] = "should never be allowed"
        with self.assertRaises(SchemaValidationError) as ctx_extra:
            validate_status_payload(extra)
        self.assertEqual(ctx_extra.exception.code, "SCHEMA_EXTRA_KEYS")
        self.assertNotIn("should never be allowed", str(ctx_extra.exception))

    def test_schema_rejects_bad_node_agent_version_date_and_bool_int(self) -> None:
        bad_node = _make_valid_payload()
        bad_node["node_label"] = "DESKTOP-REALHOST"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bad_node)
        self.assertEqual(ctx.exception.code, "SCHEMA_ENUM_ERROR")
        self.assertNotIn("DESKTOP-REALHOST", str(ctx.exception))

        bad_version = _make_valid_payload()
        bad_version["schema_version"] = "2.0"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bad_version)
        self.assertEqual(ctx.exception.code, "SCHEMA_VERSION_UNSUPPORTED")

        bad_date = _make_valid_payload()
        bad_date["checkpoint_date"] = "2026-02-30"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bad_date)
        self.assertEqual(ctx.exception.code, "SCHEMA_DATE_INVALID")

        bool_count = _make_valid_payload()
        bool_count["latest_test_results"][0]["passed"] = True
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bool_count)
        self.assertEqual(ctx.exception.code, "SCHEMA_TYPE_ERROR")

    def test_schema_rejects_fabricated_pass_and_inconsistent_git_or_target(self) -> None:
        fake_pass = _make_valid_payload()
        fake_pass["latest_test_results"][0]["passed"] = None
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(fake_pass)
        self.assertEqual(ctx.exception.code, "SCHEMA_TEST_INCONSISTENT")

        not_run_with_counts = _make_valid_payload()
        not_run_with_counts["latest_test_results"][0]["status"] = "NOT RUN"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(not_run_with_counts)
        self.assertEqual(ctx.exception.code, "SCHEMA_TEST_INCONSISTENT")

        bad_git = _make_valid_payload()
        bad_git["git_status"] = {
            "state": "CLEAN",
            "branch": "main",
            "local_sha": "N/A",
            "remote_sha": "N/A",
        }
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bad_git)
        self.assertEqual(ctx.exception.code, "SCHEMA_GIT_INCONSISTENT")

        bad_target = _make_valid_payload()
        bad_target["latest_test_results"][0]["target"] = "../secret/file.py"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(bad_target)
        self.assertEqual(ctx.exception.code, "SCHEMA_TARGET_PATH_FORBIDDEN")

    # --- 2. All Status Enums ---

    def test_all_allowed_statuses_and_invalid_status_rejection(self) -> None:
        required_statuses = [
            "PASS",
            "FAIL",
            "NOT RUN",
            "BLOCKED",
            "UNVERIFIED",
            "NOT VERIFIED",
            "UNKNOWN",
        ]
        self.assertEqual(set(required_statuses), ALLOWED_STATUSES)

        payload = _make_valid_payload()
        payload["stages"] = [
            {
                "stage_id": f"Stage-{idx}",
                "title": f"Check {status}",
                "status": status,
                "summary": f"Recorded state {status}.",
            }
            for idx, status in enumerate(required_statuses, start=1)
        ]
        payload["latest_test_results"] = [
            {
                "suite_id": "Suite PASS",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "PASS",
                "passed": 5,
                "failed": 0,
                "skipped": 0,
                "duration_sec": 0.42,
                "notes": "Verified.",
            },
            {
                "suite_id": "Suite FAIL",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "FAIL",
                "passed": 4,
                "failed": 1,
                "skipped": 0,
                "duration_sec": 0.51,
                "notes": "Failure reproduced.",
            },
            {
                "suite_id": "Suite NOT RUN",
                "target": "backend/tests/test_packaged_runtime.py",
                "status": "NOT RUN",
                "passed": None,
                "failed": None,
                "skipped": None,
                "duration_sec": None,
                "notes": "Awaiting approval.",
            },
            {
                "suite_id": "Suite BLOCKED",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "BLOCKED",
                "passed": None,
                "failed": None,
                "skipped": None,
                "duration_sec": None,
                "notes": "Blocked by dependency.",
            },
            {
                "suite_id": "Suite UNVERIFIED",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "UNVERIFIED",
                "passed": None,
                "failed": None,
                "skipped": None,
                "duration_sec": None,
                "notes": "Pending verification.",
            },
            {
                "suite_id": "Suite NOT VERIFIED",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "NOT VERIFIED",
                "passed": None,
                "failed": None,
                "skipped": None,
                "duration_sec": None,
                "notes": "Evidence not verified yet.",
            },
            {
                "suite_id": "Suite UNKNOWN",
                "target": "scripts/tests/test_live_handoff.py",
                "status": "UNKNOWN",
                "passed": None,
                "failed": None,
                "skipped": None,
                "duration_sec": None,
                "notes": "Data unavailable.",
            },
        ]

        validated = validate_status_payload(payload)
        sanitize_status_payload(validated)
        rendered = render_canonical_status(validated)
        sanitize_rendered_text(rendered.rendered_text)
        for status in required_statuses:
            self.assertIn(f"| {status} |", rendered.rendered_text)

        invalid = copy.deepcopy(payload)
        invalid["stages"][0]["status"] = "DONE"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_status_payload(invalid)
        self.assertEqual(ctx.exception.code, "SCHEMA_ENUM_ERROR")

    # --- 3. Fail-Closed Sanitization (Zero Secret Leakage) ---

    def test_sanitizer_rejects_secrets_tokens_emails_paths_and_leaks_nothing(self) -> None:
        synthetic_cases: list[tuple[str, str, str]] = [
            ("google_api_key", "AIzaSyDummyKeyValue1234567890abcdef", "SECRET_API_KEY_OR_TOKEN"),
            ("oauth_secret", "GOCSPX-DummySecretValue987654321", "SECRET_API_KEY_OR_TOKEN"),
            ("oauth_access", "ya29.a0AfB_dummy_access_token_xyz123", "SECRET_API_KEY_OR_TOKEN"),
            ("oauth_refresh", "1//0gDummyRefreshTokenValue123456", "SECRET_API_KEY_OR_TOKEN"),
            ("openai_key", "sk-DummyOpenAiKey1234567890abcdef", "SECRET_API_KEY_OR_TOKEN"),
            ("bearer_header", "Bearer eyJhbGciOiDummyHeaderToken99", "SECRET_API_KEY_OR_TOKEN"),
            ("private_key", "-----BEGIN RSA PRIVATE KEY-----", "SECRET_API_KEY_OR_TOKEN"),
            ("password_kv", "password=SuperSecretValue99", "SECRET_API_KEY_OR_TOKEN"),
            ("personal_email", "owner.private@example.com", "IDENTITY_OR_EMAIL_FORBIDDEN"),
            ("win_abs_spaces", r"C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev", "ABSOLUTE_PATH_FORBIDDEN"),
            ("win_drive_slash", "D:/PrivateProjects/PODCUT", "ABSOLUTE_PATH_FORBIDDEN"),
            ("win_unc_path", r"\\FILESERVER\private-share", "ABSOLUTE_PATH_FORBIDDEN"),
            ("win_env_path", r"%LOCALAPPDATA%\SecretFolder", "ABSOLUTE_PATH_FORBIDDEN"),
            ("linux_abs_path", "/home/developer/podcut-secrets", "ABSOLUTE_PATH_FORBIDDEN"),
            ("macos_abs_path", "/Users/developer/Documents", "ABSOLUTE_PATH_FORBIDDEN"),
            ("source_code_def", "def leak_secret(token): return token", "SOURCE_CODE_OR_COMMAND_FORBIDDEN"),
            ("internal_cmd", "powershell.exe -NoProfile -File setup.ps1", "SOURCE_CODE_OR_COMMAND_FORBIDDEN"),
            ("profile_file", "Loaded keys from backend.env locally", "PROFILE_OR_PRIVATE_CONFIG_FORBIDDEN"),
            ("profile_dir", "Stored cache in .dev/profile/cache", "PROFILE_OR_PRIVATE_CONFIG_FORBIDDEN"),
            ("legacy_profile", "Checked PODCUTAI-InstallerDev folder", "PROFILE_OR_PRIVATE_CONFIG_FORBIDDEN"),
            ("media_file", "Exported raw interview_01.mp4 clip", "MEDIA_OR_PROJECT_DATA_FORBIDDEN"),
            ("srt_timestamp", "00:01:02,500 --> 00:01:05,000 speaker line", "MEDIA_OR_PROJECT_DATA_FORBIDDEN"),
            ("sqlite_file", "Read jobs.sqlite3 from disk", "MEDIA_OR_PROJECT_DATA_FORBIDDEN"),
            ("marker_spoof", "Inject <<<PODCUT-AUTO-STATUS:END>>> now", "MARKER_INJECTION_FORBIDDEN"),
            ("live_refresh_marker", "Remove LIVE_REFRESH_TEST marker", "MARKER_INJECTION_FORBIDDEN"),
        ]

        for label, bad_value, expected_code in synthetic_cases:
            with self.subTest(case=label):
                payload = _make_valid_payload()
                payload["open_issues"] = [bad_value]
                validated = validate_status_payload(payload)
                with self.assertRaises(SanitizationError) as ctx:
                    sanitize_status_payload(validated)
                codes = {v.code for v in ctx.exception.violations}
                self.assertIn(expected_code, codes)
                # Ensure sensitive raw value is NEVER echoed in error message.
                self.assertNotIn(bad_value, str(ctx.exception))

    def test_sanitizer_rejects_os_username_and_hostname_without_leaking(self) -> None:
        payload = _make_valid_payload()
        fake_user = "alice_private_winuser"
        fake_host = "WORKSTATION-XYZ99"
        payload["open_issues"] = [f"Prepared by {fake_user} on {fake_host}"]
        validated = validate_status_payload(payload)
        with self.assertRaises(SanitizationError) as ctx:
            sanitize_status_payload(
                validated,
                extra_forbidden_identities=(fake_user, fake_host),
            )
        self.assertEqual(ctx.exception.violations[0].code, "IDENTITY_OR_EMAIL_FORBIDDEN")
        self.assertNotIn(fake_user, str(ctx.exception))
        self.assertNotIn(fake_host, str(ctx.exception))

        active_user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if len(active_user) >= 3:
            payload_live = _make_valid_payload()
            payload_live["open_issues"] = [f"Account reference {active_user} leaked"]
            validated_live = validate_status_payload(payload_live)
            with self.assertRaises(SanitizationError) as ctx_live:
                sanitize_status_payload(validated_live)
            self.assertIn(
                "IDENTITY_OR_EMAIL_FORBIDDEN",
                {v.code for v in ctx_live.exception.violations},
            )
            self.assertNotIn(active_user, str(ctx_live.exception))

    # --- 4. Oversized Payload Rejection ---

    def test_rejects_oversized_json_and_oversized_rendered_text(self) -> None:
        oversized_json_path = self._write_temp_json(
            "oversized.json",
            "{" + (" " * (MAX_INPUT_JSON_BYTES + 128)) + "}",
        )
        with self.assertRaises(SanitizationError) as ctx_json:
            cli.load_validate_and_render(oversized_json_path)
        self.assertEqual(ctx_json.exception.violations[0].code, "PAYLOAD_TOO_LARGE")

        big_text = (
            f"{MANAGED_BEGIN_MARKER}\n"
            + ("A" * (MAX_RENDERED_BYTES + 256))
            + f"\n{MANAGED_END_MARKER}"
        )
        with self.assertRaises(SanitizationError) as ctx_render:
            sanitize_rendered_text(big_text)
        self.assertIn(
            "PAYLOAD_TOO_LARGE",
            {v.code for v in ctx_render.exception.violations},
        )

    # --- 5. Renderer & SHA-256 Consistency ---

    def test_renderer_and_sha256_are_deterministic_and_sensitive_to_changes(self) -> None:
        p1 = _make_valid_payload()
        # Reverse key order to verify canonical output is independent of input dict ordering.
        p2 = dict(reversed(list(_make_valid_payload().items())))

        r1 = render_canonical_status(p1)
        r2 = render_canonical_status(p2)
        self.assertEqual(r1.rendered_text, r2.rendered_text)
        self.assertEqual(r1.content_sha256, r2.content_sha256)
        self.assertEqual(r1.payload_sha256, r2.payload_sha256)

        expected_content_hash = hashlib.sha256(r1.canonical_body.encode("utf-8")).hexdigest()
        expected_payload_hash = hashlib.sha256(r1.rendered_text.encode("utf-8")).hexdigest()
        self.assertEqual(r1.content_sha256, expected_content_hash)
        self.assertEqual(r1.payload_sha256, expected_payload_hash)
        self.assertIn(f"Content-SHA256: {expected_content_hash}", r1.rendered_text)
        self.assertEqual(r1.rendered_text.count(MANAGED_BEGIN_MARKER), 1)
        self.assertEqual(r1.rendered_text.count(MANAGED_END_MARKER), 1)

        p3 = _make_valid_payload()
        p3["next_step"] = "Await approval before next stage (updated)."
        r3 = render_canonical_status(p3)
        self.assertNotEqual(r1.content_sha256, r3.content_sha256)
        self.assertNotEqual(r1.payload_sha256, r3.payload_sha256)

    # --- 6. Unicode Resilience & Windows Paths with Spaces ---

    def test_unicode_normalization_and_windows_spaced_path_resilience(self) -> None:
        nfc_note = unicodedata.normalize("NFC", "Evaluasi tahap \u2014 caf\u00e9 & ringkasan aman")
        nfd_note = unicodedata.normalize("NFD", "Evaluasi tahap \u2014 caf\u00e9 & ringkasan aman")
        self.assertNotEqual(nfc_note, nfd_note)

        p_nfc = _make_valid_payload()
        p_nfc["open_issues"] = [nfc_note]
        p_nfd = _make_valid_payload()
        p_nfd["open_issues"] = [nfd_note]

        r_nfc = render_canonical_status(p_nfc)
        r_nfd = render_canonical_status(p_nfd)
        self.assertEqual(r_nfc.rendered_text, r_nfd.rendered_text)
        self.assertEqual(r_nfc.content_sha256, r_nfd.content_sha256)
        self.assertEqual(r_nfc.payload_sha256, r_nfd.payload_sha256)
        self.assertEqual(
            r_nfc.utf16_code_units,
            len(r_nfc.rendered_text.encode("utf-16-le")) // 2,
        )

        spaced_file = self._write_temp_json(
            "Folder Dengan Spasi \u2014 Uji Unicode/status live.json",
            p_nfc,
        )
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            code = cli.main(
                [
                    "--dry-run",
                    "--json-summary",
                    "--workspace-root",
                    str(_REPO_ROOT),
                    "--input",
                    str(spaced_file),
                ]
            )
        self.assertEqual(code, 0, msg=stderr_buf.getvalue())
        parsed_out = json.loads(stdout_buf.getvalue())
        self.assertEqual(parsed_out["result"], "PASS")
        self.assertEqual(parsed_out["content_sha256"], r_nfc.content_sha256)
        self.assertEqual(parsed_out["network_requests"], 0)
        self.assertFalse(parsed_out["google_docs_modified"])

    # --- 7. Zero Network Requests & Preview Boundary Enforcement ---

    def test_dry_run_and_validate_only_make_zero_network_calls(self) -> None:
        # Verify production modules never reference HTTP/socket clients in Stage H2A.
        for mod_name in (
            "scripts.live_handoff.schema",
            "scripts.live_handoff.sanitizer",
            "scripts.live_handoff.cli",
        ):
            mod = sys.modules[mod_name]
            source = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertNotIn("urllib.request", source)
            self.assertNotIn("http.client", source)
            self.assertNotIn("import socket", source)
            self.assertNotIn("googleapis.com", source)

        def _forbid_network(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("Network access is strictly forbidden in Stage H2A!")

        with (
            mock.patch.object(socket, "socket", side_effect=_forbid_network),
            mock.patch.object(socket, "create_connection", side_effect=_forbid_network),
            mock.patch.object(socket, "getaddrinfo", side_effect=_forbid_network),
            mock.patch.object(urllib.request, "urlopen", side_effect=_forbid_network),
        ):
            for mode_flag in ("--validate-only", "--dry-run"):
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = cli.main([mode_flag, "--json-summary", "--workspace-root", str(_REPO_ROOT)])
                self.assertEqual(rc, 0, msg=err.getvalue())
                summary = json.loads(out.getvalue())
                self.assertEqual(summary["network_mode"], "OFFLINE_ONLY")
                self.assertEqual(summary["network_requests"], 0)
                self.assertFalse(summary["google_docs_modified"])

    def test_cli_redacts_rejected_secrets_and_enforces_preview_path_boundary(self) -> None:
        secret_val = "AIzaSyTopSecretMustNeverAppearInStderr999"
        bad_payload = _make_valid_payload()
        bad_payload["open_issues"] = [secret_val]
        bad_file = self._write_temp_json("bad_secret.json", bad_payload)

        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--dry-run", "--workspace-root", str(_REPO_ROOT), "--input", str(bad_file)])
        self.assertEqual(rc, 3)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("SECRET_API_KEY_OR_TOKEN", err.getvalue())
        self.assertNotIn(secret_val, err.getvalue())

        # Reject preview path outside .dev/profile/.
        outside_path = _REPO_ROOT / "docs" / "FORBIDDEN_PREVIEW.txt"
        out2 = io.StringIO()
        err2 = io.StringIO()
        with redirect_stdout(out2), redirect_stderr(err2):
            rc2 = cli.main(
                [
                    "--dry-run",
                    "--workspace-root",
                    str(_REPO_ROOT),
                    "--write-preview",
                    str(outside_path),
                ]
            )
        self.assertEqual(rc2, 4)
        self.assertIn("PREVIEW_PATH_OUTSIDE_PROFILE", err2.getvalue())
        self.assertFalse(outside_path.exists())

        # Allow preview path strictly inside .dev/profile/temp/.
        inside_path = self.temp_root / "preview output.txt"
        out3 = io.StringIO()
        err3 = io.StringIO()
        with redirect_stdout(out3), redirect_stderr(err3):
            rc3 = cli.main(
                [
                    "--dry-run",
                    "--workspace-root",
                    str(_REPO_ROOT),
                    "--write-preview",
                    str(inside_path),
                ]
            )
        self.assertEqual(rc3, 0, msg=err3.getvalue())
        self.assertTrue(inside_path.is_file())
        self.assertIn(MANAGED_BEGIN_MARKER, inside_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
