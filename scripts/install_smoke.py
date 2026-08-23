#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise an installed TaskStateGuard package without importing the checkout."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence


EXPECTED_VERSION = "0.1.0"


class InstallFailure(RuntimeError):
    """A fixed-category installed-package verification failure."""


def _clean_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_json(
    base: Sequence[str],
    arguments: Sequence[str],
    *,
    directory: str,
    environment: Dict[str, str],
    expected_status: int = 0,
) -> Dict[str, object]:
    completed = subprocess.run(
        [*base, *arguments],
        cwd=directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    if completed.returncode != expected_status or completed.stderr:
        raise InstallFailure("cli_process_result_failed")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        raise InstallFailure("cli_json_invalid") from None
    if not isinstance(payload, dict):
        raise InstallFailure("cli_payload_invalid")
    return payload


def _console_entrypoint() -> Path:
    scripts = Path(sysconfig.get_path("scripts"))
    name = "task-state-guard.exe" if os.name == "nt" else "task-state-guard"
    entrypoint = scripts / name
    if not entrypoint.is_file():
        raise InstallFailure("console_entrypoint_missing")
    return entrypoint


def smoke() -> Dict[str, object]:
    if importlib.metadata.version("task-state-guard") != EXPECTED_VERSION:
        raise InstallFailure("distribution_version_mismatch")

    environment = _clean_environment()
    module = [sys.executable, "-I", "-m", "task_state_guard"]
    entrypoint = _console_entrypoint()
    for command, expected_output in (
        ([str(entrypoint), "--help"], None),
        ([str(entrypoint), "--version"], "task-state-guard 0.1.0\n"),
        ([*module, "--version"], "task-state-guard 0.1.0\n"),
    ):
        completed = subprocess.run(
            command,
            cwd=tempfile.gettempdir(),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        if (
            completed.returncode != 0
            or completed.stderr
            or (expected_output is not None and completed.stdout != expected_output)
        ):
            raise InstallFailure("installed_entrypoint_contract_failed")

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "ledger.sqlite"
        common: List[str] = []
        if os.name == "nt":
            common.append("--allow-external-acl")
        common.extend(("--db", str(database)))

        initialized = _run_json(
            module,
            [*common, "init"],
            directory=directory,
            environment=environment,
        )
        expected_permission = "external_acl" if os.name == "nt" else "posix_mode"
        if (
            initialized.get("ok") is not True
            or not isinstance(initialized.get("storage"), dict)
            or initialized["storage"].get("permission_model") != expected_permission
        ):
            raise InstallFailure("initialization_contract_failed")

        created = _run_json(
            module,
            [*common, "create", "--timeout-seconds", "30"],
            directory=directory,
            environment=environment,
        )
        task = created.get("task")
        if created.get("ok") is not True or not isinstance(task, dict):
            raise InstallFailure("create_contract_failed")
        task_id = task.get("id")
        if not isinstance(task_id, str):
            raise InstallFailure("task_identifier_missing")

        for arguments in (
            [*common, "start", task_id],
            [
                *common,
                "close",
                task_id,
                "--status",
                "succeeded",
                "--code",
                "completed",
            ],
            [
                *common,
                "delivery",
                task_id,
                "--status",
                "delivered",
                "--code",
                "transport_acknowledged",
            ],
        ):
            result = _run_json(
                module,
                arguments,
                directory=directory,
                environment=environment,
            )
            if result.get("ok") is not True:
                raise InstallFailure("lifecycle_contract_failed")

        doctor = _run_json(
            module,
            [*common, "doctor"],
            directory=directory,
            environment=environment,
        )
        if (
            doctor.get("healthy") is not True
            or doctor.get("quick_check") != "ok"
            or doctor.get("task_count") != 1
            or doctor.get("event_count") != 4
        ):
            raise InstallFailure("doctor_contract_failed")

    return {
        "code": "ok",
        "lifecycle_count": 4,
        "ok": True,
        "permission_model": expected_permission,
        "schema": "task-state-guard-install-smoke-v1",
        "version": EXPECTED_VERSION,
    }


def main() -> int:
    try:
        report = smoke()
    except InstallFailure as error:
        report = {
            "code": str(error),
            "lifecycle_count": 0,
            "ok": False,
            "schema": "task-state-guard-install-smoke-v1",
        }
    except Exception:
        report = {
            "code": "install_smoke_failed",
            "lifecycle_count": 0,
            "ok": False,
            "schema": "task-state-guard-install-smoke-v1",
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
