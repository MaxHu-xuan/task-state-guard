#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a deterministic, values-free restart-reconciliation demonstration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from task_state_guard import Ledger


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DISPLAY_DATABASE = "<temporary-demo>/tasks.sqlite"
SYNTHETIC_TIME = 946_684_800.0
SYNTHETIC_TASK_IDS = (
    "a11ce001-a11c-4a11-8ce0-a11ce0000001",
    "b0b00002-b0b0-4b0b-8b0b-b0b000000002",
    "cafe0003-cafe-4caf-8afe-cafe00000003",
    "dead0004-dead-4dea-8dea-dead00000004",
)
RECONCILE_ARGUMENTS = (
    "reconcile",
    "--active-grace-seconds",
    "600",
    "--delivery-grace-seconds",
    "86400",
)


class DemoFailure(RuntimeError):
    """Represent a fixed-category demonstration failure."""


def _common_arguments(database: Path, *, allow_external_acl: bool) -> List[str]:
    arguments = ["--db", str(database)]
    if allow_external_acl:
        arguments.insert(0, "--allow-external-acl")
    return arguments


def _display_command(
    arguments: Sequence[str], *, allow_external_acl: bool
) -> str:
    common = (
        ["py", "-3", "-m", "task_state_guard"]
        if os.name == "nt"
        else ["python3", "-m", "task_state_guard"]
    )
    if allow_external_acl:
        common.append("--allow-external-acl")
    common.extend(("--db", DISPLAY_DATABASE))
    return " ".join((*common, *arguments))


def _run_cli(
    database: Path,
    arguments: Sequence[str],
    *,
    allow_external_acl: bool,
) -> Dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "task_state_guard",
            *_common_arguments(database, allow_external_acl=allow_external_acl),
            *arguments,
        ],
        cwd=str(PROJECT_ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or completed.stderr:
        raise DemoFailure("cli_process_failed")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        raise DemoFailure("cli_output_invalid") from None
    if not isinstance(payload, dict):
        raise DemoFailure("cli_output_invalid")
    return payload


def _seed(database: Path, *, allow_external_acl: bool) -> None:
    ledger = Ledger(
        database,
        clock=lambda: SYNTHETIC_TIME,
        allow_external_acl=allow_external_acl,
    )
    ledger.create_task(task_id=SYNTHETIC_TASK_IDS[0])

    stale = ledger.create_task(
        task_id=SYNTHETIC_TASK_IDS[1],
        timeout_seconds=120,
    )
    ledger.start_task(stale.id)

    parent = ledger.create_task(task_id=SYNTHETIC_TASK_IDS[2])
    child = ledger.create_task(
        task_id=SYNTHETIC_TASK_IDS[3],
        parent_id=parent.id,
        delivery_required=False,
    )
    ledger.close_task(child.id, "succeeded", code="worker_completed")
    ledger.close_task(parent.id, "succeeded", code="worker_completed")


def _expected_reconcile(*, dry_run: bool, first_pass: bool) -> Dict[str, object]:
    if first_pass:
        report: Dict[str, object] = {
            "active_examined": 2,
            "applied": not dry_run,
            "deliveries_failed": 1,
            "deliveries_not_applicable": 1,
            "dry_run": dry_run,
            "fresh_active_retained": 1,
            "pending_delivery_examined": 3,
            "pending_delivery_retained": 1,
            "tasks_timed_out": 1,
        }
    else:
        report = {
            "active_examined": 1,
            "applied": True,
            "deliveries_failed": 0,
            "deliveries_not_applicable": 0,
            "dry_run": False,
            "fresh_active_retained": 1,
            "pending_delivery_examined": 1,
            "pending_delivery_retained": 1,
            "tasks_timed_out": 0,
        }
    return {"ok": True, "reconcile": report}


def _expected_doctor() -> Dict[str, object]:
    return {
        "delivery_status_counts": {
            "delivered": 0,
            "failed": 1,
            "not_applicable": 1,
            "pending": 2,
        },
        "doctor_error_count": 0,
        "event_count": 10,
        "event_inconsistency_count": 0,
        "foreign_key_error_count": 0,
        "healthy": True,
        "orphan_count": 0,
        "parent_cycle_count": 0,
        "quick_check": "ok",
        "schema_ok": True,
        "schema_version": 2,
        "state_inconsistency_count": 0,
        "task_count": 4,
        "task_status_counts": {
            "cancelled": 0,
            "failed": 0,
            "queued": 1,
            "running": 0,
            "succeeded": 2,
            "timed_out": 1,
        },
        "timestamp_inconsistency_count": 0,
    }


def _emit_step(
    label: str,
    arguments: Sequence[str],
    payload: Mapping[str, object],
    *,
    allow_external_acl: bool,
) -> None:
    print(
        f"[{label}] command: "
        + _display_command(arguments, allow_external_acl=allow_external_acl)
    )
    print(
        f"[{label}] output: "
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )


def run_demo(*, allow_external_acl: bool = False) -> None:
    if allow_external_acl != (os.name == "nt"):
        raise DemoFailure("platform_acl_acknowledgement_mismatch")
    with tempfile.TemporaryDirectory(prefix="task-state-guard-demo-") as directory:
        database = Path(directory) / "tasks.sqlite"
        _seed(database, allow_external_acl=allow_external_acl)

        preview_arguments = (*RECONCILE_ARGUMENTS, "--dry-run")
        preview = _run_cli(
            database,
            preview_arguments,
            allow_external_acl=allow_external_acl,
        )
        if preview != _expected_reconcile(dry_run=True, first_pass=True):
            raise DemoFailure("preview_result_mismatch")
        _emit_step(
            "preview",
            preview_arguments,
            preview,
            allow_external_acl=allow_external_acl,
        )

        applied = _run_cli(
            database,
            RECONCILE_ARGUMENTS,
            allow_external_acl=allow_external_acl,
        )
        if applied != _expected_reconcile(dry_run=False, first_pass=True):
            raise DemoFailure("apply_result_mismatch")
        _emit_step(
            "apply",
            RECONCILE_ARGUMENTS,
            applied,
            allow_external_acl=allow_external_acl,
        )

        repeated = _run_cli(
            database,
            RECONCILE_ARGUMENTS,
            allow_external_acl=allow_external_acl,
        )
        if repeated != _expected_reconcile(dry_run=False, first_pass=False):
            raise DemoFailure("idempotent_result_mismatch")
        _emit_step(
            "idempotent-recheck",
            RECONCILE_ARGUMENTS,
            repeated,
            allow_external_acl=allow_external_acl,
        )

        doctor_arguments = ("doctor",)
        doctor = _run_cli(
            database,
            doctor_arguments,
            allow_external_acl=allow_external_acl,
        )
        if doctor != _expected_doctor():
            raise DemoFailure("doctor_result_mismatch")
        _emit_step(
            "doctor",
            doctor_arguments,
            doctor,
            allow_external_acl=allow_external_acl,
        )


def main() -> int:
    try:
        expected_arguments = ["--allow-external-acl"] if os.name == "nt" else []
        if sys.argv[1:] != expected_arguments:
            raise DemoFailure("invalid_demo_arguments")
        run_demo(allow_external_acl=os.name == "nt")
    except DemoFailure as error:
        print(
            json.dumps(
                {"error": str(error), "ok": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print('{"error":"internal_demo_error","ok":false}', file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
