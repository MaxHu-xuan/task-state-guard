#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect built artifacts and smoke-test an offline wheel installation."""

from __future__ import annotations

import base64
import binascii
import configparser
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from email import policy
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = "task-state-guard"
EXPECTED_VERSION = "0.1.0"
EXPECTED_DIST_INFO = "task_state_guard-0.1.0.dist-info"
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
EXPECTED_SINGLETON_METADATA = {
    "Name": EXPECTED_NAME,
    "Version": EXPECTED_VERSION,
    "Requires-Python": ">=3.11",
    "License-Expression": "Apache-2.0",
}
EXPECTED_REPOSITORY_URL = (
    "Repository, https://github.com/MaxHu-xuan/task-state-guard"
)
EXPECTED_CONSOLE_ENTRYPOINTS = {
    "task-state-guard": "task_state_guard.cli:main",
}
EXPECTED_WHEEL_DESCRIPTOR = {
    "Wheel-Version": "1.0",
    "Root-Is-Purelib": "true",
    "Tag": "py3-none-any",
}
ALLOWED_WHEEL_DESCRIPTOR_HEADERS = frozenset(
    name.casefold()
    for name in (*EXPECTED_WHEEL_DESCRIPTOR, "Generator")
)
EXPECTED_TOP_LEVEL = "task_state_guard\n"
EXPECTED_PACKAGE_FILES = frozenset(
    (
        "task_state_guard/__init__.py",
        "task_state_guard/__main__.py",
        "task_state_guard/cli.py",
        "task_state_guard/model.py",
        "task_state_guard/reconcile.py",
        "task_state_guard/store.py",
    )
)
EXPECTED_WHEEL_FILES = EXPECTED_PACKAGE_FILES | frozenset(
    (
        EXPECTED_DIST_INFO + "/licenses/LICENSE",
        EXPECTED_DIST_INFO + "/METADATA",
        EXPECTED_DIST_INFO + "/WHEEL",
        EXPECTED_DIST_INFO + "/entry_points.txt",
        EXPECTED_DIST_INFO + "/top_level.txt",
        EXPECTED_DIST_INFO + "/RECORD",
    )
)
WHEEL_TO_SDIST_SOURCE = {
    **{name: "src/" + name for name in EXPECTED_PACKAGE_FILES},
    EXPECTED_DIST_INFO + "/licenses/LICENSE": "LICENSE",
}


class ArtifactFailure(RuntimeError):
    """A fixed-category artifact verification failure."""


def _normalize_newlines(value: str, error_code: str) -> str:
    """Normalize LF/CRLF text while rejecting bare carriage returns."""

    if "\x00" in value:
        raise ArtifactFailure(error_code)
    normalized = value.replace("\r\n", "\n")
    if "\r" in normalized:
        raise ArtifactFailure(error_code)
    return normalized


def _verify_core_metadata(metadata: str) -> None:
    """Validate required wheel metadata independent of platform newlines."""

    metadata = _normalize_newlines(metadata, "wheel_metadata_mismatch")
    try:
        message = Parser(policy=policy.default).parsestr(
            metadata,
            headersonly=True,
        )
    except (TypeError, ValueError):
        raise ArtifactFailure("wheel_metadata_mismatch") from None
    if message.defects:
        raise ArtifactFailure("wheel_metadata_mismatch")
    for name, expected in EXPECTED_SINGLETON_METADATA.items():
        values = message.get_all(name, [])
        if len(values) != 1 or str(values[0]) != expected:
            raise ArtifactFailure("wheel_metadata_mismatch")
    repository_urls = [str(value) for value in message.get_all("Project-URL", [])]
    repository_entries = [
        value
        for value in repository_urls
        if value.partition(",")[0].strip() == "Repository"
    ]
    if repository_entries != [EXPECTED_REPOSITORY_URL]:
        raise ArtifactFailure("wheel_metadata_mismatch")


def _verify_entrypoints(entrypoints: str) -> None:
    """Validate the console entry point as strict, newline-neutral INI."""

    entrypoints = _normalize_newlines(entrypoints, "wheel_entrypoint_mismatch")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(entrypoints)
    except configparser.Error:
        raise ArtifactFailure("wheel_entrypoint_mismatch") from None
    if (
        parser.defaults()
        or parser.sections() != ["console_scripts"]
        or dict(parser.items("console_scripts")) != EXPECTED_CONSOLE_ENTRYPOINTS
    ):
        raise ArtifactFailure("wheel_entrypoint_mismatch")


def _verify_wheel_descriptor(descriptor: str) -> None:
    """Validate installer-relevant WHEEL fields without pinning Generator."""

    descriptor = _normalize_newlines(descriptor, "wheel_descriptor_mismatch")
    try:
        message = Parser(policy=policy.default).parsestr(descriptor)
    except (TypeError, ValueError):
        raise ArtifactFailure("wheel_descriptor_mismatch") from None
    payload = message.get_payload()
    header_names = [name.casefold() for name in message.keys()]
    if (
        message.defects
        or message.is_multipart()
        or payload not in (None, "")
        or any(
            name not in ALLOWED_WHEEL_DESCRIPTOR_HEADERS
            for name in header_names
        )
    ):
        raise ArtifactFailure("wheel_descriptor_mismatch")
    for name, expected in EXPECTED_WHEEL_DESCRIPTOR.items():
        values = message.get_all(name, [])
        if len(values) != 1 or str(values[0]) != expected:
            raise ArtifactFailure("wheel_descriptor_mismatch")
    generators = [str(value) for value in message.get_all("Generator", [])]
    if len(generators) > 1:
        raise ArtifactFailure("wheel_descriptor_mismatch")
    if generators:
        generator = generators[0]
        if (
            not 1 <= len(generator) <= 200
            or generator != generator.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in generator)
        ):
            raise ArtifactFailure("wheel_descriptor_mismatch")


def _verify_top_level(top_level: str) -> None:
    """Require the wheel to expose only the expected import package."""

    normalized = _normalize_newlines(top_level, "wheel_top_level_mismatch")
    if normalized != EXPECTED_TOP_LEVEL:
        raise ArtifactFailure("wheel_top_level_mismatch")


def _parts(name: str) -> Tuple[str, ...]:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
    ):
        raise ArtifactFailure("unsafe_archive_member")
    raw_parts = name.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in raw_parts):
        raise ArtifactFailure("unsafe_archive_member")
    path = PurePosixPath(name)
    if path.is_absolute() or any(
        part in ("", ".", "..") or ":" in part for part in path.parts
    ):
        raise ArtifactFailure("unsafe_archive_member")
    return path.parts


def _artifacts(directory: Path) -> Tuple[Path, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise ArtifactFailure("invalid_artifact_directory")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ArtifactFailure("unexpected_artifact_set")
    if wheels[0].is_symlink() or sdists[0].is_symlink():
        raise ArtifactFailure("artifact_alias")
    return wheels[0], sdists[0]


def _verify_record(
    archive: zipfile.ZipFile,
    file_names: set[str],
    record_name: str,
) -> None:
    """Verify the wheel RECORD as a strict SHA-256 closed set."""

    try:
        raw_record = archive.read(record_name).decode("utf-8", "strict")
        rows = list(csv.reader(io.StringIO(raw_record, newline=""), strict=True))
    except (csv.Error, KeyError, UnicodeError):
        raise ArtifactFailure("wheel_record_mismatch") from None

    recorded_names = set()
    for row in rows:
        if len(row) != 3:
            raise ArtifactFailure("wheel_record_mismatch")
        name, encoded_digest, encoded_size = row
        _parts(name)
        if name in recorded_names:
            raise ArtifactFailure("wheel_record_mismatch")
        recorded_names.add(name)
        if name == record_name:
            if encoded_digest or encoded_size:
                raise ArtifactFailure("wheel_record_mismatch")
            continue
        if not encoded_digest.startswith("sha256="):
            raise ArtifactFailure("wheel_record_mismatch")
        digest_text = encoded_digest.partition("=")[2]
        if not digest_text or "=" in digest_text:
            raise ArtifactFailure("wheel_record_mismatch")
        try:
            digest = base64.b64decode(
                digest_text + "=" * (-len(digest_text) % 4),
                altchars=b"-_",
                validate=True,
            )
            size = int(encoded_size)
            payload = archive.read(name)
        except (KeyError, ValueError, binascii.Error):
            raise ArtifactFailure("wheel_record_mismatch") from None
        if (
            base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            != digest_text
            or digest != hashlib.sha256(payload).digest()
            or size < 0
            or encoded_size != str(size)
            or size != len(payload)
        ):
            raise ArtifactFailure("wheel_record_mismatch")
    if recorded_names != file_names:
        raise ArtifactFailure("wheel_record_mismatch")


def _verify_wheel(wheel: Path) -> int:
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            if not infos:
                raise ArtifactFailure("empty_wheel")
            names = set()
            file_names = set()
            total_bytes = 0
            for info in infos:
                normalized = (
                    info.filename[:-1]
                    if info.is_dir() and info.filename.endswith("/")
                    else info.filename
                )
                parts = _parts(normalized)
                if normalized in names:
                    raise ArtifactFailure("duplicate_wheel_member")
                names.add(normalized)
                if not info.is_dir():
                    file_names.add(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if (
                    file_type not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or (info.is_dir() and file_type not in (0, stat.S_IFDIR))
                    or (not info.is_dir() and file_type == stat.S_IFDIR)
                    or info.flag_bits & 0x1
                ):
                    raise ArtifactFailure("unsafe_wheel_member")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ArtifactFailure("oversized_wheel_member")
                total_bytes += info.file_size
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise ArtifactFailure("oversized_wheel")
                first = parts[0]
                if first != "task_state_guard" and not first.endswith(".dist-info"):
                    raise ArtifactFailure("unexpected_wheel_member")

            metadata_names = sorted(
                name for name in names if name.endswith(".dist-info/METADATA")
            )
            entrypoint_names = sorted(
                name for name in names if name.endswith(".dist-info/entry_points.txt")
            )
            record_names = sorted(
                name for name in names if name.endswith(".dist-info/RECORD")
            )
            directory_names = names - file_names
            if file_names != EXPECTED_WHEEL_FILES or any(
                not any(
                    expected.startswith(directory + "/")
                    for expected in EXPECTED_WHEEL_FILES
                )
                for directory in directory_names
            ):
                raise ArtifactFailure("unexpected_wheel_member")
            if (
                len(metadata_names) != 1
                or len(entrypoint_names) != 1
                or record_names != [EXPECTED_DIST_INFO + "/RECORD"]
                or metadata_names != [EXPECTED_DIST_INFO + "/METADATA"]
                or entrypoint_names != [EXPECTED_DIST_INFO + "/entry_points.txt"]
            ):
                raise ArtifactFailure("wheel_content_missing")

            metadata = archive.read(metadata_names[0]).decode("utf-8", "strict")
            _verify_core_metadata(metadata)
            entrypoints = archive.read(entrypoint_names[0]).decode("utf-8", "strict")
            _verify_entrypoints(entrypoints)
            descriptor = archive.read(EXPECTED_DIST_INFO + "/WHEEL").decode(
                "utf-8", "strict"
            )
            _verify_wheel_descriptor(descriptor)
            top_level = archive.read(
                EXPECTED_DIST_INFO + "/top_level.txt"
            ).decode("utf-8", "strict")
            _verify_top_level(top_level)
            _verify_record(archive, file_names, record_names[0])
            return sum(not info.is_dir() for info in infos)
    except (OSError, UnicodeError, zipfile.BadZipFile):
        raise ArtifactFailure("wheel_read_failed") from None


def _verify_source_consistency(wheel: Path, sdist_root: Path) -> None:
    """Require wheel runtime sources and license to equal the audited sdist."""

    try:
        with zipfile.ZipFile(wheel) as archive:
            for wheel_name, sdist_name in WHEEL_TO_SDIST_SOURCE.items():
                if archive.read(wheel_name) != (sdist_root / sdist_name).read_bytes():
                    raise ArtifactFailure("artifact_source_mismatch")
    except ArtifactFailure:
        raise
    except (KeyError, OSError, zipfile.BadZipFile):
        raise ArtifactFailure("artifact_source_mismatch") from None


def _extract_sdist(sdist: Path, destination: Path) -> Tuple[Path, int]:
    try:
        with tarfile.open(sdist, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ArtifactFailure("empty_sdist")
            roots = set()
            file_count = 0
            total_bytes = 0
            for member in members:
                normalized = (
                    member.name[:-1]
                    if member.isdir() and member.name.endswith("/")
                    else member.name
                )
                parts = _parts(normalized)
                roots.add(parts[0])
                if not (member.isdir() or member.isreg()):
                    raise ArtifactFailure("unsafe_sdist_member")
                if member.size > MAX_MEMBER_BYTES:
                    raise ArtifactFailure("oversized_sdist_member")
                total_bytes += member.size
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise ArtifactFailure("oversized_sdist")
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ArtifactFailure("sdist_member_unreadable")
                with source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                file_count += 1
    except (OSError, tarfile.TarError):
        raise ArtifactFailure("sdist_read_failed") from None
    if len(roots) != 1:
        raise ArtifactFailure("sdist_root_mismatch")
    root = destination / next(iter(roots))
    if not root.is_dir():
        raise ArtifactFailure("sdist_root_missing")
    return root, file_count


def _run(
    arguments: Sequence[str],
    *,
    environment: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments),
        cwd=str(PROJECT_ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _audit_sdist(root: Path) -> None:
    completed = _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "privacy_audit.py"),
            "--sdist",
            str(root),
        ]
    )
    if completed.returncode != 0 or completed.stderr:
        raise ArtifactFailure("sdist_privacy_audit_failed")
    try:
        report = json.loads(completed.stdout)
    except (TypeError, ValueError):
        raise ArtifactFailure("sdist_privacy_report_invalid") from None
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise ArtifactFailure("sdist_privacy_audit_failed")


def _venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _install_wheel(wheel: Path, directory: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"

    venv.EnvBuilder(with_pip=True, clear=True).create(directory)
    interpreter = _venv_python(directory)
    scripts = interpreter.parent
    environment["PATH"] = str(scripts) + os.pathsep + environment.get("PATH", "")
    installed = _run(
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        environment=environment,
    )
    if installed.returncode != 0:
        raise ArtifactFailure("offline_wheel_install_failed")
    smoke = _run(
        [
            str(interpreter),
            str(PROJECT_ROOT / "scripts" / "install_smoke.py"),
        ],
        environment=environment,
    )
    if smoke.returncode != 0 or smoke.stderr:
        raise ArtifactFailure("installed_wheel_smoke_failed")
    try:
        report = json.loads(smoke.stdout)
    except (TypeError, ValueError):
        raise ArtifactFailure("installed_wheel_report_invalid") from None
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise ArtifactFailure("installed_wheel_smoke_failed")


def smoke(directory: Path) -> Dict[str, object]:
    wheel, sdist = _artifacts(directory.resolve(strict=True))
    wheel_files = _verify_wheel(wheel)
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        source_root, sdist_files = _extract_sdist(
            sdist, temporary_root / "source"
        )
        _audit_sdist(source_root)
        _verify_source_consistency(wheel, source_root)
        _install_wheel(wheel, temporary_root / "environment")
    return {
        "artifact_count": 2,
        "code": "ok",
        "ok": True,
        "schema": "task-state-guard-artifact-smoke-v1",
        "sdist_file_count": sdist_files,
        "version": EXPECTED_VERSION,
        "wheel_file_count": wheel_files,
    }


def main(argv: Optional[List[str]] = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    directory = Path(arguments[0]) if len(arguments) == 1 else PROJECT_ROOT / "dist"
    try:
        if len(arguments) > 1:
            raise ArtifactFailure("invalid_arguments")
        report = smoke(directory)
    except (ArtifactFailure, OSError, subprocess.SubprocessError, ValueError) as error:
        report = {
            "artifact_count": 0,
            "code": str(error) if isinstance(error, ArtifactFailure) else "artifact_smoke_failed",
            "ok": False,
            "schema": "task-state-guard-artifact-smoke-v1",
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
