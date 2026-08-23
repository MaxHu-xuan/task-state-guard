# SPDX-License-Identifier: Apache-2.0
"""Command-line interface with JSON metadata output and no network behavior."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional, Sequence

from .model import DeliveryStatus, InvalidInput, LedgerError, TaskStatus
from .reconcile import ReconcilePolicy, reconcile_restart
from .store import Ledger


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


class SafeArgumentParser(argparse.ArgumentParser):
    """Raise a value-free domain error instead of echoing rejected arguments."""

    def error(self, message: str) -> None:
        raise InvalidInput("invalid command-line arguments")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="task-state-guard",
        description="Crash-safe task metadata without plaintext payload storage.",
    )
    parser.add_argument("--db", required=True, help="Path to the local SQLite database")
    parser.add_argument(
        "--allow-external-acl",
        action="store_true",
        help="Acknowledge externally enforced filesystem ACLs on non-POSIX hosts",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the database")

    create = subparsers.add_parser("create", help="Create or idempotently reuse a task")
    create.add_argument("--task-id", help="Optional caller-generated UUID")
    create.add_argument("--parent-id", help="Existing parent task UUID")
    create.add_argument("--payload-sha256", help="Opaque lowercase SHA-256 fingerprint")
    create.add_argument("--timeout-seconds", type=float)
    create.add_argument(
        "--internal",
        action="store_true",
        help="Mark delivery as owned by a parent or external flow",
    )

    for name in ("start", "heartbeat"):
        command = subparsers.add_parser(name)
        command.add_argument("task_id")

    close = subparsers.add_parser("close")
    close.add_argument("task_id")
    close.add_argument(
        "--status",
        required=True,
        choices=[
            status.value
            for status in TaskStatus
            if status.value not in ("queued", "running")
        ],
    )
    close.add_argument("--code")

    delivery = subparsers.add_parser("delivery")
    delivery.add_argument("task_id")
    delivery.add_argument(
        "--status",
        required=True,
        choices=[
            DeliveryStatus.DELIVERED.value,
            DeliveryStatus.FAILED.value,
            DeliveryStatus.NOT_APPLICABLE.value,
        ],
    )
    delivery.add_argument("--code")

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--active-grace-seconds", type=float, default=600.0)
    reconcile.add_argument("--delivery-grace-seconds", type=float, default=600.0)

    subparsers.add_parser("doctor", help="Emit aggregate counts only")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        ledger = Ledger(args.db, allow_external_acl=args.allow_external_acl)
        if args.command == "init":
            _emit({"ok": True, "storage": ledger.storage_info()})
        elif args.command == "create":
            task = ledger.create_task(
                task_id=args.task_id,
                parent_id=args.parent_id,
                payload_hash=args.payload_sha256,
                timeout_seconds=args.timeout_seconds,
                delivery_required=not args.internal,
            )
            _emit({"ok": True, "task": task.to_dict()})
        elif args.command == "start":
            _emit({"ok": True, "task": ledger.start_task(args.task_id).to_dict()})
        elif args.command == "heartbeat":
            _emit({"ok": True, "task": ledger.heartbeat(args.task_id).to_dict()})
        elif args.command == "close":
            task = ledger.close_task(args.task_id, args.status, code=args.code)
            _emit({"ok": True, "task": task.to_dict()})
        elif args.command == "delivery":
            task = ledger.set_delivery(args.task_id, args.status, code=args.code)
            _emit({"ok": True, "task": task.to_dict()})
        elif args.command == "reconcile":
            policy = ReconcilePolicy(
                active_grace_seconds=args.active_grace_seconds,
                delivery_grace_seconds=args.delivery_grace_seconds,
            )
            _emit({"ok": True, "reconcile": reconcile_restart(ledger, policy)})
        elif args.command == "doctor":
            report = ledger.doctor()
            _emit(report)
            if report.get("healthy") is not True:
                return 1
        else:
            raise AssertionError("unreachable command")
        return 0
    except LedgerError as exc:
        _emit({"ok": False, "error": type(exc).__name__})
        return 2
    except Exception:
        _emit({"ok": False, "error": "InternalError"})
        return 3


if __name__ == "__main__":
    sys.exit(main())
