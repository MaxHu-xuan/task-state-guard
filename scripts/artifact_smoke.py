#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect built artifacts and smoke-test an offline wheel installation."""

from __future__ import annotations

import configparser
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


def _parts(name: str) -> Tuple[str, ...]:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
    ):
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


def _verify_wheel(wheel: Path) -> int:
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            if not infos:
                raise ArtifactFailure("empty_wheel")
            names = set()
            total_bytes = 0
            for info in infos:
                parts = _parts(info.filename)
                normalized = info.filename.rstrip("/")
                if normalized in names:
                    raise ArtifactFailure("duplicate_wheel_member")
                names.add(normalized)
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
            if (
                "task_state_guard/__init__.py" not in names
                or len(metadata_names) != 1
                or len(entrypoint_names) != 1
            ):
                raise ArtifactFailure("wheel_content_missing")

            metadata = archive.read(metadata_names[0]).decode("utf-8", "strict")
            _verify_core_metadata(metadata)
            entrypoints = archive.read(entrypoint_names[0]).decode("utf-8", "strict")
            _verify_entrypoints(entrypoints)
            return sum(not info.is_dir() for info in infos)
    except (OSError, UnicodeError, zipfile.BadZipFile):
        raise ArtifactFailure("wheel_read_failed") from None


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
                parts = _parts(member.name.rstrip("/"))
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
