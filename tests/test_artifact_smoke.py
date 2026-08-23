# SPDX-License-Identifier: Apache-2.0
"""Cross-platform regression tests for built-artifact verification."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "task_state_guard_artifact_smoke",
    PROJECT_ROOT / "scripts" / "artifact_smoke.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("artifact smoke module is unavailable")
ARTIFACT_SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARTIFACT_SMOKE)


def _metadata(newline: str = "\n") -> str:
    return newline.join(
        (
            "Metadata-Version: 2.4",
            "Name: task-state-guard",
            "Version: 0.1.0",
            "Requires-Python: >=3.11",
            "License-Expression: Apache-2.0",
            "Project-URL: Homepage, https://github.com/MaxHu-xuan/task-state-guard",
            "Project-URL: Repository, https://github.com/MaxHu-xuan/task-state-guard",
            "",
            "Synthetic package description.",
        )
    )


class CoreMetadataTestCase(unittest.TestCase):
    def test_accepts_lf_and_windows_crlf(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                ARTIFACT_SMOKE._verify_core_metadata(_metadata(newline))

    def test_rejects_missing_or_duplicated_singleton_headers(self):
        missing = _metadata().replace("License-Expression: Apache-2.0\n", "")
        duplicated = _metadata().replace(
            "Name: task-state-guard\n",
            "Name: task-state-guard\nName: task-state-guard\n",
        )
        for value in (missing, duplicated):
            with self.subTest():
                with self.assertRaisesRegex(
                    ARTIFACT_SMOKE.ArtifactFailure,
                    "^wheel_metadata_mismatch$",
                ):
                    ARTIFACT_SMOKE._verify_core_metadata(value)

    def test_rejects_repository_url_present_only_in_body(self):
        header = (
            "Project-URL: Repository, "
            "https://github.com/MaxHu-xuan/task-state-guard\n"
        )
        value = _metadata().replace(header, "") + "\n" + header
        with self.assertRaisesRegex(
            ARTIFACT_SMOKE.ArtifactFailure,
            "^wheel_metadata_mismatch$",
        ):
            ARTIFACT_SMOKE._verify_core_metadata(value)

    def test_rejects_duplicate_repository_url(self):
        header = (
            "Project-URL: Repository, "
            "https://github.com/MaxHu-xuan/task-state-guard\n"
        )
        value = _metadata().replace(header, header + header)
        with self.assertRaisesRegex(
            ARTIFACT_SMOKE.ArtifactFailure,
            "^wheel_metadata_mismatch$",
        ):
            ARTIFACT_SMOKE._verify_core_metadata(value)

    def test_rejects_bare_carriage_returns(self):
        for value in (_metadata("\r"), _metadata().replace("Name:", "Name:\r")):
            with self.subTest():
                with self.assertRaisesRegex(
                    ARTIFACT_SMOKE.ArtifactFailure,
                    "^wheel_metadata_mismatch$",
                ):
                    ARTIFACT_SMOKE._verify_core_metadata(value)


class WheelRegressionTestCase(unittest.TestCase):
    def test_crlf_wheel_metadata_and_entrypoint_pass_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "synthetic.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("task_state_guard/__init__.py", "")
                archive.writestr(
                    "task_state_guard-0.1.0.dist-info/METADATA",
                    _metadata("\r\n"),
                )
                archive.writestr(
                    "task_state_guard-0.1.0.dist-info/entry_points.txt",
                    "[console_scripts]\r\n"
                    "task-state-guard = task_state_guard.cli:main\r\n",
                )

            self.assertEqual(ARTIFACT_SMOKE._verify_wheel(wheel), 3)

    def test_entrypoint_parser_rejects_duplicates_and_body_spoofing(self):
        values = (
            "[console_scripts]\n"
            "task-state-guard = task_state_guard.cli:main\n"
            "task-state-guard = task_state_guard.cli:main\n",
            "[unrelated]\n"
            "note = task-state-guard = task_state_guard.cli:main\n",
        )
        for value in values:
            with self.subTest():
                with self.assertRaisesRegex(
                    ARTIFACT_SMOKE.ArtifactFailure,
                    "^wheel_entrypoint_mismatch$",
                ):
                    ARTIFACT_SMOKE._verify_entrypoints(value)


if __name__ == "__main__":
    unittest.main()
