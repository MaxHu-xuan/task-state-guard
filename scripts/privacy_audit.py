#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Values-free pre-publication audit for the TaskStateGuard release tree."""

from __future__ import annotations

import argparse
import ast
import collections
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Counter, Dict, Iterator, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LICENSE_SHA256 = (
    "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
)
MAX_FILE_BYTES = 1_048_576
_POSIX_MODE_AUDIT = os.name == "posix"
SKIP_DIRECTORIES = frozenset((".git",))
SDIST_EGG_INFO_DIRECTORY = "src/task_state_guard.egg-info"
RESIDUE_DIRECTORIES = frozenset(
    (
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
    )
)
DATA_SUFFIXES = frozenset(
    (
        ".avro",
        ".csv",
        ".db",
        ".docx",
        ".eml",
        ".gif",
        ".gz",
        ".har",
        ".jpeg",
        ".jpg",
        ".jsonl",
        ".key",
        ".log",
        ".mbox",
        ".msg",
        ".ndjson",
        ".p12",
        ".parquet",
        ".pcap",
        ".pdf",
        ".pem",
        ".pfx",
        ".png",
        ".ppt",
        ".pptx",
        ".pyc",
        ".sqlite",
        ".sqlite3",
        ".tsv",
        ".webp",
        ".whl",
        ".zip",
    )
)
FORBIDDEN_NAMES = frozenset(
    (".DS_Store", ".env", "credentials.json", "secrets.json")
)
TEXT_SUFFIXES = frozenset(
    (
        "",
        ".cfg",
        ".in",
        ".ini",
        ".json",
        ".md",
        ".py",
        ".rst",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    )
)
TEXT_NAMES = frozenset((".gitignore", "LICENSE", "MANIFEST.in"))
REQUIRED_FILES = (
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "PROVENANCE.md",
    "README.md",
    "RELEASING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THREAT_MODEL.md",
    "pyproject.toml",
    "scripts/canonicalize_sdist.py",
    "scripts/artifact_smoke.py",
    "scripts/install_smoke.py",
    "scripts/privacy_audit.py",
    "src/task_state_guard/__init__.py",
    "src/task_state_guard/__main__.py",
    "src/task_state_guard/cli.py",
    "src/task_state_guard/model.py",
    "src/task_state_guard/reconcile.py",
    "src/task_state_guard/store.py",
    "tests/test_artifact_smoke.py",
    "tests/test_task_state_guard.py",
)
REPOSITORY_REQUIRED_FILES = (
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/dependabot.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".gitignore",
)
NETWORK_OR_TELEMETRY_MODULES = frozenset(
    (
        "aiohttp",
        "analytics",
        "boto3",
        "ddtrace",
        "ftplib",
        "http",
        "httpx",
        "opentelemetry",
        "paramiko",
        "posthog",
        "requests",
        "sentry_sdk",
        "socket",
        "urllib",
        "urllib3",
    )
)


def _joined(*parts: str) -> str:
    """Keep synthetic secret signatures out of this script's source text."""

    return "".join(parts)


CONTENT_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    (
        "source.absolute_home_path",
        re.compile(_joined(r"/(?:", "Users", r"|home)/[^/\s]+/")),
    ),
    (
        "source.privileged_home_path",
        re.compile(_joined(r"/", "root", r"/")),
    ),
    (
        "source.email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "source.phone_number",
        re.compile(r"(?<![A-Za-z0-9])(?:\+?\d[ -]?){10,15}(?![A-Za-z0-9])"),
    ),
    (
        "source.ipv4_address",
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    ),
    (
        "source.provider_key",
        re.compile(
            _joined(
                r"(?<![A-Za-z0-9])(?:",
                "s",
                r"k-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{12,}|gh[pousr]_[A-Za-z0-9]{20,})",
                r"(?![A-Za-z0-9])",
            )
        ),
    ),
    (
        "source.private_key",
        re.compile(
            _joined(
                r"-----BEGIN ",
                r"(?:RSA |EC |OPENSSH )?",
                "PRIVATE KEY",
                r"-----",
            )
        ),
    ),
    (
        "source.credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
            r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
        ),
    ),
)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _files(
    root: Path,
    findings: Counter[Tuple[str, str]],
    *,
    sdist: bool = False,
) -> Iterator[Path]:
    def on_walk_error(error: OSError) -> None:
        raw_path = getattr(error, "filename", None)
        relative = "."
        if isinstance(raw_path, str):
            try:
                candidate = Path(raw_path)
                candidate_relative = candidate.relative_to(root)
                if all(
                    part not in ("", ".", "..")
                    for part in candidate_relative.parts
                ):
                    relative = candidate_relative.as_posix()
            except (OSError, ValueError):
                pass
        findings[(relative, "scan.directory_error")] += 1

    for directory, names, files in os.walk(
        str(root), topdown=True, onerror=on_walk_error, followlinks=False
    ):
        base = Path(directory)
        kept = []
        for name in sorted(names):
            candidate = base / name
            relative = _relative(candidate, root)
            if candidate.is_symlink():
                findings[(relative, "artifact.symlink")] += 1
            elif name in RESIDUE_DIRECTORIES:
                findings[(relative, "artifact.generated_directory")] += 1
            elif name.endswith(".egg-info"):
                if sdist and relative == SDIST_EGG_INFO_DIRECTORY:
                    kept.append(name)
                else:
                    findings[(relative, "artifact.generated_directory")] += 1
            elif name not in SKIP_DIRECTORIES:
                kept.append(name)
        names[:] = kept
        for name in sorted(files):
            yield base / name


def _metadata_checks(
    root: Path,
    findings: Counter[Tuple[str, str]],
    *,
    sdist: bool,
) -> None:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            findings[(relative, "release.required_file_missing")] += 1
    if not sdist:
        for relative in REPOSITORY_REQUIRED_FILES:
            if not (root / relative).is_file():
                findings[(relative, "release.required_file_missing")] += 1

    license_path = root / "LICENSE"
    try:
        license_bytes = license_path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        pass
    else:
        if hashlib.sha256(license_bytes).hexdigest() != EXPECTED_LICENSE_SHA256:
            findings[("LICENSE", "license.apache_2_0_mismatch")] += 1

    try:
        metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    checks = (
        (
            r"(?m)^name\s*=\s*['\"]task-state-guard['\"]\s*$",
            "metadata.distribution_name_mismatch",
        ),
        (
            r"(?m)^task-state-guard\s*=\s*['\"]task_state_guard\.cli:main['\"]\s*$",
            "metadata.cli_entrypoint_mismatch",
        ),
        (
            r"(?m)^license\s*=\s*['\"]Apache-2\.0['\"]\s*$",
            "metadata.license_expression_missing",
        ),
        (
            r"(?m)^license-files\s*=\s*\[\s*['\"]LICENSE['\"]\s*\]\s*$",
            "metadata.license_file_missing",
        ),
        (
            r"(?m)^dependencies\s*=\s*\[\s*\]\s*$",
            "metadata.runtime_dependencies_present",
        ),
        (r"(?m)^keywords\s*=\s*\[", "metadata.keywords_missing"),
        (
            r"(?m)^Repository\s*=\s*['\"]https://github\.com/MaxHu-xuan/task-state-guard['\"]\s*$",
            "metadata.repository_url_missing",
        ),
        (
            r"Operating System :: Microsoft :: Windows",
            "metadata.windows_classifier_missing",
        ),
        (
            r"Operating System :: MacOS",
            "metadata.macos_classifier_missing",
        ),
        (
            r"Operating System :: POSIX :: Linux",
            "metadata.linux_classifier_missing",
        ),
        (r"setuptools>=77\.0\.3", "metadata.build_backend_too_old"),
        (
            r'''(?m)^package-dir\s*=\s*\{\s*(?:""|'')\s*=\s*['\"]src['\"]\s*\}\s*$''',
            "metadata.package_directory_mismatch",
        ),
        (
            r"(?m)^where\s*=\s*\[\s*['\"]src['\"]\s*\]\s*$",
            "metadata.package_discovery_mismatch",
        ),
    )
    for pattern, code in checks:
        if not re.search(pattern, metadata):
            findings[("pyproject.toml", code)] += 1
    if "License :: OSI Approved :: Apache Software License" in metadata:
        findings[("pyproject.toml", "metadata.pep639_classifier_conflict")] += 1

    try:
        manifest_lines = set(
            (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        )
    except (OSError, UnicodeError):
        return
    expected_manifest_lines = {
        "include CHANGELOG.md",
        "include CODE_OF_CONDUCT.md",
        "include CONTRIBUTING.md",
        "include LICENSE",
        "include PROVENANCE.md",
        "include README.md",
        "include RELEASING.md",
        "include SECURITY.md",
        "include SUPPORT.md",
        "include THREAT_MODEL.md",
        "include scripts/canonicalize_sdist.py",
        "include scripts/artifact_smoke.py",
        "include scripts/install_smoke.py",
        "include scripts/privacy_audit.py",
        "recursive-include tests *.py",
    }
    for _entry in sorted(expected_manifest_lines - manifest_lines):
        findings[("MANIFEST.in", "metadata.sdist_entry_missing")] += 1


def _imported_modules(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".", 1)[0]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            yield node.args[0].value.split(".", 1)[0]


def _inspect_python(
    relative: str,
    text: str,
    findings: Counter[Tuple[str, str]],
) -> None:
    if "# SPDX-License-Identifier: Apache-2.0" not in text.splitlines()[:5]:
        findings[(relative, "license.spdx_missing")] += 1
    try:
        tree = ast.parse(text, filename=relative)
    except SyntaxError:
        findings[(relative, "source.python_syntax_error")] += 1
        return
    blocked = set(_imported_modules(tree)) & NETWORK_OR_TELEMETRY_MODULES
    if blocked:
        findings[(relative, "source.network_or_telemetry_import")] += len(blocked)


def _report(
    findings: Counter[Tuple[str, str]], files_scanned: int
) -> Dict[str, object]:
    rows = [
        {"path": path, "code": code, "count": count}
        for (path, code), count in sorted(findings.items())
    ]
    return {
        "schema": "task-state-guard-privacy-audit-v1",
        "ok": not rows,
        "files_scanned": files_scanned,
        "finding_count": sum(row["count"] for row in rows),
        "findings": rows,
        "permission_check": (
            "posix_mode" if _POSIX_MODE_AUDIT else "external_acl_not_inspected"
        ),
    }


def audit(
    root: Path,
    validate_release: bool = True,
    *,
    sdist: bool = False,
) -> Dict[str, object]:
    findings: Counter[Tuple[str, str]] = collections.Counter()
    files_scanned = 0
    try:
        if root.is_symlink() or not root.is_dir():
            findings[(".", "scan.invalid_root")] += 1
            return _report(findings, files_scanned)
        root = root.resolve(strict=True)
    except OSError:
        findings[(".", "scan.invalid_root")] += 1
        return _report(findings, files_scanned)

    if validate_release:
        _metadata_checks(root, findings, sdist=sdist)
    if sdist:
        expected_egg_info = root / SDIST_EGG_INFO_DIRECTORY
        try:
            valid_egg_info = (
                not expected_egg_info.is_symlink() and expected_egg_info.is_dir()
            )
        except OSError:
            valid_egg_info = False
        if not valid_egg_info:
            findings[
                (SDIST_EGG_INFO_DIRECTORY, "release.sdist_egg_info_missing")
            ] += 1

    for path in _files(root, findings, sdist=sdist):
        relative = _relative(path, root)
        try:
            metadata = path.lstat()
        except OSError:
            findings[(relative, "scan.metadata_error")] += 1
            continue
        if stat.S_ISLNK(metadata.st_mode):
            findings[(relative, "artifact.symlink")] += 1
            continue
        if not stat.S_ISREG(metadata.st_mode):
            findings[(relative, "artifact.non_regular")] += 1
            continue
        if metadata.st_nlink != 1:
            findings[(relative, "artifact.hardlink")] += 1
        if (
            _POSIX_MODE_AUDIT
            and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            findings[(relative, "artifact.unsafe_write_permissions")] += 1
        files_scanned += 1
        if path.name in FORBIDDEN_NAMES:
            findings[(relative, "artifact.forbidden_name")] += 1
        if path.suffix.lower() in DATA_SUFFIXES:
            findings[(relative, "artifact.persistent_data")] += 1
            continue
        if metadata.st_size > MAX_FILE_BYTES:
            findings[(relative, "artifact.oversized")] += 1
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            findings[(relative, "artifact.binary_or_unknown")] += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            findings[(relative, "scan.text_error")] += 1
            continue
        for code, pattern in CONTENT_PATTERNS:
            count = sum(1 for _match in pattern.finditer(text))
            if count:
                findings[(relative, code)] += count
        if path.suffix.lower() == ".py":
            _inspect_python(relative, text, findings)
    return _report(findings, files_scanned)


def self_test() -> bool:
    release_report = audit(PROJECT_ROOT)
    if not release_report["ok"]:
        return False

    with tempfile.TemporaryDirectory() as directory:
        temporary_root = Path(directory)
        root = temporary_root / "canaries"
        root.mkdir()
        sensitive_values = (
            "/" + "Users" + "/sample-user/notes",
            "/" + "root" + "/private/notes",
            "person" + "@" + "example.invalid",
            "139" + "\u0660" * 4 + "\u0661" * 4,
            "192" + ".0.2.1",
            "s" + "k-" + "A" * 24,
            "-----BEGIN " + "PRIVATE KEY-----",
            "pass" + "word = 'synthetic-value'",
        )
        (root / "sample.txt").write_text(
            "\n".join(sensitive_values), encoding="utf-8"
        )
        (root / "network.py").write_text("import requests\n", encoding="utf-8")
        (root / "fixture.db").write_bytes(b"synthetic")
        (root / ".env").write_text("synthetic\n", encoding="utf-8")
        unsafe = root / "unsafe.txt"
        unsafe.write_text("synthetic\n", encoding="utf-8")
        unsafe.chmod(0o666)
        (root / "__pycache__").mkdir()

        report = audit(root, validate_release=False)
        codes = {row["code"] for row in report["findings"]}
        expected = {
            "artifact.forbidden_name",
            "artifact.generated_directory",
            "artifact.persistent_data",
            "license.spdx_missing",
            "source.absolute_home_path",
            "source.credential_assignment",
            "source.email_address",
            "source.ipv4_address",
            "source.network_or_telemetry_import",
            "source.phone_number",
            "source.private_key",
            "source.privileged_home_path",
            "source.provider_key",
        }
        if _POSIX_MODE_AUDIT:
            expected.add("artifact.unsafe_write_permissions")
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
        invalid_marker = "synthetic_private_" + "x" * 24
        invalid = json.dumps(
            audit(root / invalid_marker), sort_keys=True, separators=(",", ":")
        )
        canary_checks_ok = (
            not report["ok"]
            and expected.issubset(codes)
            and not any(value in encoded for value in sensitive_values)
            and invalid_marker not in invalid
        )

        sdist_root = temporary_root / "sdist-clean"
        egg_info = sdist_root / SDIST_EGG_INFO_DIRECTORY
        egg_info.mkdir(parents=True)
        (egg_info / "PKG-INFO").write_text(
            "Metadata-Version: 2.4\nName: task-state-guard\n",
            encoding="utf-8",
        )
        ordinary_report = audit(sdist_root, validate_release=False)
        ordinary_findings = {
            (row["path"], row["code"]) for row in ordinary_report["findings"]
        }
        sdist_report = audit(sdist_root, validate_release=False, sdist=True)

        scan_marker = "audit-person" + "@" + "example.invalid"
        (egg_info / "scan.txt").write_text(scan_marker, encoding="utf-8")
        scanned_report = audit(sdist_root, validate_release=False, sdist=True)
        scanned_codes = {row["code"] for row in scanned_report["findings"]}
        scanned_encoded = json.dumps(
            scanned_report, sort_keys=True, separators=(",", ":")
        )

        impostor_root = temporary_root / "sdist-impostor"
        impostor = impostor_root / "src/task_state_guard_copy.egg-info"
        impostor.mkdir(parents=True)
        (impostor / "PKG-INFO").write_text("synthetic\n", encoding="utf-8")
        impostor_report = audit(
            impostor_root, validate_release=False, sdist=True
        )
        impostor_findings = {
            (row["path"], row["code"]) for row in impostor_report["findings"]
        }

        extra_root = temporary_root / "sdist-extra"
        allowed = extra_root / SDIST_EGG_INFO_DIRECTORY
        allowed.mkdir(parents=True)
        (allowed / "PKG-INFO").write_text("synthetic\n", encoding="utf-8")
        extra = extra_root / "src/unexpected.egg-info"
        extra.mkdir()
        (extra / "PKG-INFO").write_text("synthetic\n", encoding="utf-8")
        extra_report = audit(extra_root, validate_release=False, sdist=True)
        extra_findings = {
            (row["path"], row["code"]) for row in extra_report["findings"]
        }

        walk_root = temporary_root / "walk-root"
        walk_root.mkdir()
        walk_root = walk_root.resolve(strict=True)
        original_walk = os.walk

        def denied_walk(*args, **kwargs):
            del args
            onerror = kwargs.get("onerror")
            if onerror is not None:
                onerror(
                    OSError(
                        errno.EACCES,
                        "synthetic directory error",
                        str(walk_root / "blocked"),
                    )
                )
            return iter(())

        os.walk = denied_walk
        try:
            walk_report = audit(walk_root, validate_release=False)
        finally:
            os.walk = original_walk
        walk_findings = {
            (row["path"], row["code"]) for row in walk_report["findings"]
        }

        return (
            canary_checks_ok
            and not ordinary_report["ok"]
            and (
                SDIST_EGG_INFO_DIRECTORY,
                "artifact.generated_directory",
            )
            in ordinary_findings
            and sdist_report["ok"]
            and sdist_report["files_scanned"] == 1
            and not scanned_report["ok"]
            and "source.email_address" in scanned_codes
            and scan_marker not in scanned_encoded
            and not impostor_report["ok"]
            and (
                "src/task_state_guard_copy.egg-info",
                "artifact.generated_directory",
            )
            in impostor_findings
            and (
                SDIST_EGG_INFO_DIRECTORY,
                "release.sdist_egg_info_missing",
            )
            in impostor_findings
            and not extra_report["ok"]
            and (
                "src/unexpected.egg-info",
                "artifact.generated_directory",
            )
            in extra_findings
            and not walk_report["ok"]
            and ("blocked", "scan.directory_error") in walk_findings
        )


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed arguments without echoing attacker-controlled values."""

    def error(self, message: str) -> None:
        raise ValueError("invalid arguments")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = SafeArgumentParser(
        description="Audit a release tree without echoing matched source content"
    )
    parser.add_argument("root", nargs="?", default=str(PROJECT_ROOT))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-test", action="store_true")
    modes.add_argument(
        "--sdist",
        action="store_true",
        help="Audit an extracted sdist with its exact generated egg-info directory",
    )
    try:
        args = parser.parse_args(argv)
        if args.self_test:
            ok = self_test()
            report = {
                "schema": "task-state-guard-privacy-self-test-v1",
                "ok": ok,
                "code": "ok" if ok else "self_test_failed",
                "count": 0 if ok else 1,
            }
        else:
            report = audit(Path(args.root), sdist=args.sdist)
    except (OSError, TypeError, ValueError):
        report = {
            "schema": "task-state-guard-privacy-audit-v1",
            "ok": False,
            "files_scanned": 0,
            "finding_count": 1,
            "findings": [
                {"path": ".", "code": "scan.invalid_arguments", "count": 1}
            ],
            "permission_check": (
                "posix_mode"
                if _POSIX_MODE_AUDIT
                else "external_acl_not_inspected"
            ),
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
