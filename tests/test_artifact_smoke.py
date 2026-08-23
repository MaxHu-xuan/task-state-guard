# SPDX-License-Identifier: Apache-2.0
"""Cross-platform regression tests for built-artifact verification."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
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
DIST_INFO = "task_state_guard-0.1.0.dist-info"
RECORD_NAME = DIST_INFO + "/RECORD"


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


def _wheel_descriptor(newline: str = "\n") -> str:
    return newline.join(
        (
            "Wheel-Version: 1.0",
            "Generator: setuptools (synthetic)",
            "Root-Is-Purelib: true",
            "Tag: py3-none-any",
            "",
        )
    )


def _wheel_members(newline: str) -> dict[str, bytes]:
    members = {name: b"" for name in ARTIFACT_SMOKE.EXPECTED_PACKAGE_FILES}
    members.update(
        {
            DIST_INFO + "/licenses/LICENSE": b"synthetic license\n",
            DIST_INFO + "/METADATA": _metadata(newline).encode("utf-8"),
            DIST_INFO + "/WHEEL": _wheel_descriptor(newline).encode("utf-8"),
            DIST_INFO + "/entry_points.txt": (
                "[console_scripts]"
                + newline
                + "task-state-guard = task_state_guard.cli:main"
                + newline
            ).encode("utf-8"),
            DIST_INFO + "/top_level.txt": (
                "task_state_guard" + newline
            ).encode("utf-8"),
        }
    )
    return members


def _record_rows(members: dict[str, bytes]) -> list[list[str]]:
    rows = []
    for name, payload in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        rows.append(
            [
                name,
                "sha256=" + digest.rstrip(b"=").decode("ascii"),
                str(len(payload)),
            ]
        )
    rows.append([RECORD_NAME, "", ""])
    return rows


def _write_wheel(
    wheel: Path,
    *,
    newline: str = "\n",
    transform_record=None,
    extra_members=None,
) -> None:
    members = _wheel_members(newline)
    if extra_members is not None:
        members.update(extra_members)
    rows = _record_rows(members)
    if transform_record is not None:
        transform_record(rows)
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    members[RECORD_NAME] = output.getvalue().encode("utf-8")
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


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
    def test_lf_and_crlf_wheels_pass_end_to_end_with_valid_record(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                with tempfile.TemporaryDirectory() as directory:
                    wheel = Path(directory) / "synthetic.whl"
                    _write_wheel(wheel, newline=newline)
                    self.assertEqual(ARTIFACT_SMOKE._verify_wheel(wheel), 12)

    def test_valid_record_cannot_hide_an_extra_wheel_member(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "synthetic.whl"
            _write_wheel(
                wheel,
                extra_members={DIST_INFO + "/unexpected.txt": b"synthetic"},
            )
            with self.assertRaisesRegex(
                ARTIFACT_SMOKE.ArtifactFailure,
                "^unexpected_wheel_member$",
            ):
                ARTIFACT_SMOKE._verify_wheel(wheel)

    def test_valid_record_cannot_hide_wheel_descriptor_changes(self):
        cases = (
            _wheel_descriptor().replace("1.0", "2.0", 1),
            _wheel_descriptor().replace(
                "Wheel-Version: 1.0\n",
                "Wheel-Version: 1.0\nWheel-Version: 1.0\n",
            ),
            _wheel_descriptor().replace("true", "false", 1),
            _wheel_descriptor().replace("py3-none-any", "cp312-cp312-any", 1),
            _wheel_descriptor().replace(
                "Tag: py3-none-any\n",
                "Tag: py3-none-any\nTag: py3-none-any\n",
            ),
            _wheel_descriptor().replace(
                "Root-Is-Purelib: true\n",
                "Root-Is-Purelib: true\nBuild: 1\n",
            ),
            _wheel_descriptor().replace(
                "Generator: setuptools (synthetic)\n",
                "Generator: setuptools (synthetic)\nGenerator: other\n",
            ),
            _wheel_descriptor() + "\nsynthetic body",
        )
        for index, descriptor in enumerate(cases):
            with self.subTest(case=index):
                with tempfile.TemporaryDirectory() as directory:
                    wheel = Path(directory) / "synthetic.whl"
                    _write_wheel(
                        wheel,
                        extra_members={
                            DIST_INFO + "/WHEEL": descriptor.encode("utf-8")
                        },
                    )
                    with self.assertRaisesRegex(
                        ARTIFACT_SMOKE.ArtifactFailure,
                        "^wheel_descriptor_mismatch$",
                    ):
                        ARTIFACT_SMOKE._verify_wheel(wheel)

    def test_valid_record_cannot_hide_top_level_changes(self):
        for value in (
            b"other_package\n",
            b"task_state_guard\nother_package\n",
            b"task_state_guard",
            b"task_state_guard\r",
        ):
            with self.subTest():
                with tempfile.TemporaryDirectory() as directory:
                    wheel = Path(directory) / "synthetic.whl"
                    _write_wheel(
                        wheel,
                        extra_members={
                            DIST_INFO + "/top_level.txt": value
                        },
                    )
                    with self.assertRaisesRegex(
                        ARTIFACT_SMOKE.ArtifactFailure,
                        "^wheel_top_level_mismatch$",
                    ):
                        ARTIFACT_SMOKE._verify_wheel(wheel)

    def test_valid_record_cannot_hide_source_or_license_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "synthetic.whl"
            source = root / "source"
            _write_wheel(wheel)
            with zipfile.ZipFile(wheel) as archive:
                for wheel_name, source_name in (
                    ARTIFACT_SMOKE.WHEEL_TO_SDIST_SOURCE.items()
                ):
                    target = source / source_name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(wheel_name))
            ARTIFACT_SMOKE._verify_source_consistency(wheel, source)

            for source_name in (
                "src/task_state_guard/model.py",
                "LICENSE",
            ):
                target = source / source_name
                original = target.read_bytes()
                target.write_bytes(original + b"synthetic mismatch")
                with self.assertRaisesRegex(
                    ARTIFACT_SMOKE.ArtifactFailure,
                    "^artifact_source_mismatch$",
                ):
                    ARTIFACT_SMOKE._verify_source_consistency(wheel, source)
                target.write_bytes(original)

    def test_rejects_record_hash_size_and_closed_set_mismatches(self):
        def wrong_hash(rows):
            rows[0][1] = "sha256=" + "A" * 43

        def wrong_size(rows):
            rows[0][2] = str(int(rows[0][2]) + 1)

        def missing_path(rows):
            rows.pop(0)

        def extra_path(rows):
            rows.insert(-1, ["task_state_guard/extra.py", "sha256=" + "A" * 43, "0"])

        def duplicate_path(rows):
            rows.insert(1, list(rows[0]))

        def padded_base64(rows):
            rows[0][1] += "="

        def hashed_record(rows):
            rows[-1][1] = "sha256=" + "A" * 43

        for transform in (
            wrong_hash,
            wrong_size,
            missing_path,
            extra_path,
            duplicate_path,
            padded_base64,
            hashed_record,
        ):
            with self.subTest(transform=transform.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    wheel = Path(directory) / "synthetic.whl"
                    _write_wheel(wheel, transform_record=transform)
                    with self.assertRaisesRegex(
                        ARTIFACT_SMOKE.ArtifactFailure,
                        "^wheel_record_mismatch$",
                    ):
                        ARTIFACT_SMOKE._verify_wheel(wheel)

    def test_rejects_ambiguous_archive_paths(self):
        for name in ("pkg//file.py", "pkg/./file.py", "./pkg/file.py"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    ARTIFACT_SMOKE.ArtifactFailure,
                    "^unsafe_archive_member$",
                ):
                    ARTIFACT_SMOKE._parts(name)

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
