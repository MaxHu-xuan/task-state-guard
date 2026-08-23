# SPDX-License-Identifier: Apache-2.0

import concurrent.futures
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from task_state_guard import (
    DeliveryStatus,
    InvalidInput,
    Ledger,
    ReconcilePolicy,
    StateConflict,
    TaskNotFound,
    TaskStatus,
    payload_fingerprint,
    reconcile_restart,
)
from task_state_guard.cli import main as cli_main
from task_state_guard.store import _is_link_like


_WORKER_STAGES = frozenset(("initialize", "create", "start", "close", "delivery"))
_WORKER_ERROR_CATEGORIES = frozenset(
    (
        "database_configuration",
        "database_connection",
        "path_identity",
        "sidecar_multiple_links",
        "sidecar_nonregular",
        "sidecar_inspection",
        "sidecar_permissions",
        "sidecar_symlink",
        "sqlite_busy",
        "sqlite_locked",
        "sqlite_operational",
        "state_conflict",
        "unexpected",
    )
)


def _ledger(path, **kwargs):
    kwargs.setdefault("allow_external_acl", os.name == "nt")
    return Ledger(path, **kwargs)


def _platform_cli_arguments(arguments):
    arguments = list(arguments)
    if os.name == "nt":
        return ["--allow-external-acl", *arguments]
    return arguments


@contextmanager
def _sqlite_connection(path):
    """Manage both the SQLite transaction and the connection lifetime."""

    connection = sqlite3.connect(str(path))
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _worker_error_category(error):
    if isinstance(error, sqlite3.OperationalError):
        sqlite_code = getattr(error, "sqlite_errorcode", None)
        if sqlite_code == getattr(sqlite3, "SQLITE_BUSY", 5):
            return "sqlite_busy"
        if sqlite_code == getattr(sqlite3, "SQLITE_LOCKED", 6):
            return "sqlite_locked"
        return "sqlite_operational"
    if isinstance(error, StateConflict):
        fixed_categories = {
            "database configuration failed": "database_configuration",
            "database connection failed": "database_connection",
            "database file identity changed": "path_identity",
            "SQLite sidecar cannot be inspected safely": "sidecar_inspection",
            "SQLite sidecar must not have multiple links": "sidecar_multiple_links",
            "SQLite sidecar must be a regular file": "sidecar_nonregular",
            "SQLite sidecar must not be a symbolic link": "sidecar_symlink",
            "SQLite sidecar permissions cannot be secured": "sidecar_permissions",
        }
        return fixed_categories.get(str(error), "state_conflict")
    return "unexpected"


def _worker_diagnostic(stage, error):
    return json.dumps(
        {
            "category": _worker_error_category(error),
            "ok": False,
            "stage": stage,
        },
        sort_keys=True,
    )


def _multiprocess_writer(database, count):
    stage = "initialize"
    try:
        ledger = _ledger(database, busy_timeout_ms=15_000)
        for _ in range(count):
            stage = "create"
            task = ledger.create_task(task_id=str(uuid.uuid4()))
            stage = "start"
            ledger.start_task(task.id)
            stage = "close"
            ledger.close_task(task.id, "succeeded", code="worker_completed")
            stage = "delivery"
            ledger.set_delivery(
                task.id, "delivered", code="transport_acknowledged"
            )
    except Exception as error:
        sys.stdout.write(_worker_diagnostic(stage, error) + "\n")
        return 2
    return 0


class FakeClock:
    def __init__(self, value=1_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "ledger.sqlite"
        self.clock = FakeClock()
        self.ledger = _ledger(self.db, clock=self.clock, busy_timeout_ms=2500)

    def tearDown(self):
        self.tempdir.cleanup()

    def _run_cli(self, arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(_platform_cli_arguments(arguments))
        return result, stdout.getvalue(), stderr.getvalue()

    def _assert_marker_absent_from_storage(self, marker, database=None):
        database = Path(database or self.db)
        with _sqlite_connection(database) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(database) + suffix)
            if candidate.exists():
                self.assertNotIn(marker.encode("utf-8"), candidate.read_bytes())

    def test_storage_is_private_wal_and_healthy(self):
        info = self.ledger.storage_info()
        self.assertEqual(info["journal_mode"].lower(), "wal")
        self.assertEqual(info["busy_timeout_ms"], 2500)
        self.assertTrue(info["foreign_keys"])
        self.assertEqual(
            info["permission_model"],
            "posix_mode" if os.name == "posix" else "external_acl",
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        with _sqlite_connection(self.db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.db) + suffix)
                self.assertTrue(sidecar.exists())
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
            connection.execute("ROLLBACK")
        report = self.ledger.doctor()
        self.assertTrue(report["healthy"])
        self.assertEqual(report["quick_check"], "ok")

    def test_ledger_closes_every_internal_connection_on_success_and_error(self):
        database = Path(self.tempdir.name) / "connection-lifecycle.sqlite"
        real_connect = sqlite3.connect
        opened = []

        class TrackingConnection(sqlite3.Connection):
            close_calls = 0

            def close(self):
                self.close_calls += 1
                return super().close()

        def tracked_connect(*args, **kwargs):
            kwargs["factory"] = TrackingConnection
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with mock.patch(
            "task_state_guard.store.sqlite3.connect", side_effect=tracked_connect
        ):
            ledger = _ledger(database, clock=self.clock)
            task = ledger.create_task()
            ledger.get_task(task.id)
            with self.assertRaises(TaskNotFound):
                ledger.get_task(str(uuid.uuid4()))
            ledger.doctor()
            ledger.storage_info()

        self.assertTrue(opened)
        self.assertTrue(all(connection.close_calls == 1 for connection in opened))
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_existing_database_mode_is_tightened_without_temp_artifacts(self):
        directory = Path(self.tempdir.name) / "private-state"
        directory.mkdir(mode=0o700)
        database = directory / "existing.sqlite"
        with _sqlite_connection(database):
            pass
        if os.name == "posix":
            database.chmod(0o666)

        ledger = _ledger(database, clock=self.clock)
        task = ledger.create_task()
        ledger.start_task(task.id)
        ledger.close_task(task.id, "succeeded", code="worker_completed")
        ledger.set_delivery(
            task.id, "delivered", code="transport_acknowledged"
        )

        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
        allowed_names = {
            database.name,
            database.name + "-journal",
            database.name + "-shm",
            database.name + "-wal",
        }
        self.assertLessEqual({path.name for path in directory.iterdir()}, allowed_names)

    def test_non_posix_acl_boundary_is_explicit_and_fail_closed(self):
        database = Path(self.tempdir.name) / "external-acl" / "ledger.sqlite"
        database.parent.mkdir()
        with mock.patch("task_state_guard.store._POSIX_MODE_SECURITY", False):
            with self.assertRaises(InvalidInput):
                Ledger(database)
            self.assertFalse(database.exists())

            ledger = Ledger(database, allow_external_acl=True)
            self.assertEqual(
                ledger.storage_info()["permission_model"], "external_acl"
            )

        cli_database = database.parent / "cli.sqlite"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("task_state_guard.store._POSIX_MODE_SECURITY", False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = cli_main(["--db", str(cli_database), "init"])
            self.assertEqual(result, 2)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                stdout.getvalue(), '{"error":"InvalidInput","ok":false}\n'
            )
            self.assertFalse(cli_database.exists())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = cli_main(
                    [
                        "--allow-external-acl",
                        "--db",
                        str(cli_database),
                        "init",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["storage"]["permission_model"],
                "external_acl",
            )

        with self.assertRaises(InvalidInput):
            Ledger(
                Path(self.tempdir.name) / "invalid-policy.sqlite",
                allow_external_acl="yes",
            )

    def test_windows_network_path_is_rejected_before_filesystem_access(self):
        with mock.patch("task_state_guard.store._WINDOWS_PLATFORM", True):
            with self.assertRaises(InvalidInput) as caught:
                Ledger(
                    "//synthetic.invalid/private/ledger.sqlite",
                    allow_external_acl=True,
                )
        self.assertEqual(str(caught.exception), "network database paths are unsupported")

    def test_terminal_close_is_idempotent_and_immutable(self):
        task = self.ledger.create_task()
        self.ledger.start_task(task.id)
        first = self.ledger.close_task(task.id, "succeeded", code="worker_completed")
        second = self.ledger.close_task(task.id, TaskStatus.SUCCEEDED, code="ignored_retry")
        self.assertEqual(first.status, TaskStatus.SUCCEEDED)
        self.assertEqual(second.version, first.version)
        self.assertEqual(second.terminal_code, "worker_completed")
        with self.assertRaises(StateConflict):
            self.ledger.close_task(task.id, "failed")
        with self.assertRaises(StateConflict):
            self.ledger.start_task(task.id)

    def test_task_and_event_write_roll_back_together(self):
        task = self.ledger.create_task()
        self.ledger.start_task(task.id)
        original_event = Ledger.__dict__["_event"]

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("synthetic transaction interruption")

        Ledger._event = staticmethod(fail_event)
        try:
            with self.assertRaises(RuntimeError):
                self.ledger.close_task(task.id, "failed", code="worker_failed")
        finally:
            Ledger._event = original_event

        retained = self.ledger.get_task(task.id)
        self.assertEqual(retained.status, TaskStatus.RUNNING)
        self.assertEqual(retained.version, 1)
        report = self.ledger.doctor()
        self.assertTrue(report["healthy"])
        self.assertEqual(report["event_count"], 2)

    def test_create_is_idempotent_for_same_uuid_and_immutable_fields(self):
        identifier = str(uuid.uuid4())
        digest = payload_fingerprint("synthetic")
        first = self.ledger.create_task(task_id=identifier, payload_hash=digest)
        second = self.ledger.create_task(task_id=identifier, payload_hash=digest)
        self.assertEqual(first, second)
        with self.assertRaises(StateConflict):
            self.ledger.create_task(task_id=identifier, payload_hash=None)

    def test_explicit_deadline_times_out(self):
        task = self.ledger.create_task(timeout_seconds=30)
        running = self.ledger.start_task(task.id)
        self.assertEqual(running.deadline_at, self.clock.value + 30)
        self.clock.advance(31)
        report = reconcile_restart(
            self.ledger,
            ReconcilePolicy(active_grace_seconds=999, delivery_grace_seconds=60),
        )
        self.assertEqual(report["tasks_timed_out"], 1)
        closed = self.ledger.get_task(task.id)
        self.assertEqual(closed.status, TaskStatus.TIMED_OUT)
        self.assertEqual(closed.terminal_code, "deadline_exceeded")

    def test_restart_grace_retains_fresh_then_closes_stale_running(self):
        queued = self.ledger.create_task()
        running = self.ledger.create_task()
        self.ledger.start_task(running.id)
        self.clock.advance(9)
        fresh = self.ledger.reconcile(
            active_grace_seconds=10, delivery_grace_seconds=60
        )
        self.assertEqual(fresh["tasks_timed_out"], 0)
        self.assertEqual(fresh["fresh_active_retained"], 2)
        self.clock.advance(1)
        stale = self.ledger.reconcile(
            active_grace_seconds=10, delivery_grace_seconds=60
        )
        self.assertEqual(stale["tasks_timed_out"], 1)
        self.assertEqual(self.ledger.get_task(running.id).status, TaskStatus.TIMED_OUT)
        self.assertEqual(self.ledger.get_task(queued.id).status, TaskStatus.QUEUED)

    def test_cancel_from_queue_is_terminal(self):
        task = self.ledger.create_task()
        cancelled = self.ledger.close_task(task.id, "cancelled", code="caller_cancelled")
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertIsNotNone(cancelled.ended_at)

    def test_parent_child_relationship_and_missing_parent(self):
        parent = self.ledger.create_task()
        child = self.ledger.create_task(parent_id=parent.id, delivery_required=False)
        children = self.ledger.children_of(parent.id)
        self.assertEqual([item.id for item in children], [child.id])
        self.assertEqual(child.parent_id, parent.id)
        with self.assertRaises(TaskNotFound):
            self.ledger.create_task(parent_id=str(uuid.uuid4()))

    def test_delivery_is_independent_and_reconciles_honestly(self):
        external = self.ledger.create_task(delivery_required=True)
        internal = self.ledger.create_task(parent_id=external.id, delivery_required=False)
        delivered = self.ledger.create_task(delivery_required=True)
        for task in (external, internal, delivered):
            self.ledger.close_task(task.id, "succeeded")
        first_delivery = self.ledger.set_delivery(
            delivered.id, "delivered", code="transport_acknowledged"
        )
        same_delivery = self.ledger.set_delivery(delivered.id, "delivered")
        self.assertEqual(first_delivery.version, same_delivery.version)
        self.clock.advance(11)
        report = self.ledger.reconcile(
            active_grace_seconds=10, delivery_grace_seconds=10
        )
        self.assertEqual(report["deliveries_failed"], 1)
        self.assertEqual(report["deliveries_not_applicable"], 1)
        self.assertEqual(
            self.ledger.get_task(external.id).delivery_status, DeliveryStatus.FAILED
        )
        self.assertEqual(
            self.ledger.get_task(internal.id).delivery_status,
            DeliveryStatus.NOT_APPLICABLE,
        )
        self.assertEqual(
            self.ledger.get_task(delivered.id).delivery_status,
            DeliveryStatus.DELIVERED,
        )

    def test_concurrent_same_terminal_close_is_idempotent(self):
        task = self.ledger.create_task()
        self.ledger.start_task(task.id)

        def close_once(_):
            return self.ledger.close_task(
                task.id, "failed", code="worker_failed"
            ).status

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(close_once, range(36)))
        self.assertEqual(set(statuses), {TaskStatus.FAILED})
        report = self.ledger.doctor()
        self.assertEqual(report["event_count"], 3)
        self.assertEqual(report["task_status_counts"]["failed"], 1)

    def test_independent_process_writers_remain_consistent(self):
        worker_count = 4
        tasks_per_worker = 8
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--ledger-worker",
                    str(self.db),
                    str(tasks_per_worker),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(worker_count)
        ]
        process_timeout = 60 if os.name == "nt" else 30
        outcomes = [
            process.communicate(timeout=process_timeout) for process in processes
        ]
        return_codes = [process.returncode for process in processes]
        diagnostics = []
        for stdout, stderr in outcomes:
            self.assertEqual(stderr, "")
            self.assertLessEqual(len(stdout), 128)
            if not stdout:
                diagnostics.append(None)
                continue
            diagnostic = json.loads(stdout)
            self.assertEqual(set(diagnostic), {"category", "ok", "stage"})
            self.assertIs(diagnostic["ok"], False)
            self.assertIn(diagnostic["stage"], _WORKER_STAGES)
            self.assertIn(diagnostic["category"], _WORKER_ERROR_CATEGORIES)
            diagnostics.append(diagnostic)
        self.assertEqual(
            return_codes,
            [0] * worker_count,
            {"return_codes": return_codes, "diagnostics": diagnostics},
        )
        report = self.ledger.doctor()
        self.assertTrue(report["healthy"])
        self.assertEqual(report["task_count"], worker_count * tasks_per_worker)

    def test_worker_diagnostics_are_fixed_and_value_free(self):
        marker = "synthetic_private_" + uuid.uuid4().hex
        errors = (
            RuntimeError(marker),
            StateConflict(marker),
            sqlite3.OperationalError(marker),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                rendered = _worker_diagnostic("create", error)
                self.assertNotIn(marker, rendered)
                diagnostic = json.loads(rendered)
                self.assertEqual(set(diagnostic), {"category", "ok", "stage"})
                self.assertIn(diagnostic["category"], _WORKER_ERROR_CATEGORIES)
                self.assertEqual(diagnostic["stage"], "create")
                self.assertIs(diagnostic["ok"], False)

    def test_plaintext_payload_is_never_stored_or_emitted(self):
        plaintext = "synthetic private prompt that must never reach sqlite"
        digest = payload_fingerprint(plaintext)
        task = self.ledger.create_task(payload_hash=digest)
        self.ledger.close_task(task.id, "failed", code="worker_failed")
        doctor_json = json.dumps(self.ledger.doctor(), sort_keys=True)
        task_json = json.dumps(self.ledger.get_task(task.id).to_dict(), sort_keys=True)
        self.assertNotIn(plaintext, doctor_json)
        self.assertNotIn(plaintext, task_json)
        with _sqlite_connection(self.db) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for path in (self.db, Path(str(self.db) + "-wal"), Path(str(self.db) + "-shm")):
            if path.exists():
                self.assertNotIn(plaintext.encode("utf-8"), path.read_bytes())

    def test_doctor_is_counts_only(self):
        task = self.ledger.create_task(payload_hash=payload_fingerprint("fixture"))
        rendered = json.dumps(self.ledger.doctor(), sort_keys=True)
        self.assertNotIn(task.id, rendered)
        self.assertNotIn(task.payload_hash, rendered)
        self.assertIn("task_status_counts", rendered)

    def test_invalid_values_are_rejected_without_storage(self):
        with self.assertRaises(InvalidInput):
            self.ledger.create_task(payload_hash="not-a-hash")
        with self.assertRaises(InvalidInput):
            self.ledger.create_task(timeout_seconds=-1)
        task = self.ledger.create_task()
        with self.assertRaises(InvalidInput):
            self.ledger.close_task(task.id, "running")
        with self.assertRaises(InvalidInput):
            self.ledger.close_task(task.id, "failed", code="contains spaces")

    def test_doctor_detects_timestamp_inconsistency(self):
        task = self.ledger.create_task()
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET updated_at = created_at - 1 WHERE id = ?", (task.id,)
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["timestamp_inconsistency_count"], 1)

    def test_cli_doctor_does_not_emit_task_identifiers(self):
        task = self.ledger.create_task()
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli_main(
                _platform_cli_arguments(["--db", str(self.db), "doctor"])
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertNotIn(task.id, output.getvalue())
        self.assertEqual(payload["task_count"], 1)

    def test_cli_doctor_is_deterministic_and_unhealthy_is_nonzero(self):
        self.ledger.create_task()
        arguments = ["--db", str(self.db), "doctor"]
        first_code, first_stdout, first_stderr = self._run_cli(arguments)
        second_code, second_stdout, second_stderr = self._run_cli(arguments)
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual((first_stderr, second_stderr), ("", ""))
        self.assertEqual(first_stdout, second_stdout)
        healthy_report = json.loads(first_stdout)
        self.assertEqual(
            first_stdout,
            json.dumps(healthy_report, sort_keys=True, separators=(",", ":")) + "\n",
        )
        self.assertTrue(healthy_report["healthy"])

        with _sqlite_connection(self.db) as connection:
            connection.execute("UPDATE tasks SET updated_at = created_at - 1")
        unhealthy_code, unhealthy_stdout, unhealthy_stderr = self._run_cli(arguments)
        unhealthy_report = json.loads(unhealthy_stdout)
        self.assertEqual(unhealthy_code, 1)
        self.assertEqual(unhealthy_stderr, "")
        self.assertFalse(unhealthy_report["healthy"])
        self.assertEqual(
            unhealthy_stdout,
            json.dumps(unhealthy_report, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def test_symlink_database_is_rejected_without_touching_target(self):
        target = Path(self.tempdir.name) / "unrelated.sqlite"
        with _sqlite_connection(target) as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
        target.chmod(0o644)
        before_mode = stat.S_IMODE(target.stat().st_mode)
        alias = Path(self.tempdir.name) / "alias.sqlite"
        try:
            alias.symlink_to(target)
        except OSError as error:
            if os.name != "nt":
                raise
            self.assertIn(getattr(error, "winerror", None), (5, 1314))
            return

        with mock.patch("task_state_guard.store.os.open") as open_database:
            with self.assertRaises(InvalidInput):
                _ledger(alias)
        open_database.assert_not_called()

        self.assertEqual(stat.S_IMODE(target.stat().st_mode), before_mode)
        with _sqlite_connection(target) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertNotIn("tasks", names)

    def test_observable_symlink_metadata_is_rejected_on_every_platform(self):
        alias = Path(self.tempdir.name) / "simulated-alias.sqlite"
        alias.write_bytes(b"")
        canonical_alias = alias.parent.resolve() / alias.name
        real_lstat = Path.lstat
        values = list(alias.lstat())
        values[0] = stat.S_IFLNK | 0o777
        symlink_metadata = os.stat_result(values)

        def lstat_with_alias(path):
            if path == canonical_alias:
                return symlink_metadata
            return real_lstat(path)

        with mock.patch.object(Path, "lstat", lstat_with_alias), mock.patch(
            "task_state_guard.store.os.open"
        ) as open_database:
            with self.assertRaises(InvalidInput):
                _ledger(alias)
        open_database.assert_not_called()

    def test_observable_windows_reparse_metadata_is_rejected(self):
        alias = Path(self.tempdir.name) / "simulated-reparse.sqlite"
        alias.write_bytes(b"")
        canonical_alias = alias.parent.resolve() / alias.name
        real_lstat = Path.lstat
        reparse_metadata = mock.Mock()
        reparse_metadata.st_mode = alias.lstat().st_mode
        reparse_metadata.st_file_attributes = 0x400

        def lstat_with_alias(path):
            if path == canonical_alias:
                return reparse_metadata
            return real_lstat(path)

        with mock.patch.object(Path, "lstat", lstat_with_alias), mock.patch(
            "task_state_guard.store.os.open"
        ) as open_database:
            with self.assertRaises(InvalidInput):
                _ledger(alias)
        open_database.assert_not_called()

    def test_none_windows_file_attributes_are_not_a_reparse_point(self):
        metadata = mock.Mock()
        metadata.st_mode = stat.S_IFREG | 0o600
        metadata.st_file_attributes = None

        self.assertFalse(_is_link_like(metadata))

    def test_observable_windows_reparse_parent_is_rejected(self):
        parent = Path(self.tempdir.name) / "simulated-reparse-parent"
        parent.mkdir()
        canonical_parent = parent.resolve()
        database = parent / "ledger.sqlite"
        real_lstat = Path.lstat
        reparse_metadata = mock.Mock()
        reparse_metadata.st_mode = parent.lstat().st_mode
        reparse_metadata.st_file_attributes = 0x400

        def lstat_with_parent(path):
            if path == canonical_parent:
                return reparse_metadata
            return real_lstat(path)

        with mock.patch.object(Path, "lstat", lstat_with_parent):
            with self.assertRaises(InvalidInput):
                _ledger(database)
        self.assertFalse(database.exists())

    def test_new_parent_directories_are_private(self):
        nested = Path(self.tempdir.name) / "new" / "state"
        if os.name == "posix":
            _ledger(nested / "ledger.sqlite")
            self.assertEqual(stat.S_IMODE(nested.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o700)
        else:
            with self.assertRaises(InvalidInput):
                _ledger(nested / "ledger.sqlite")
            self.assertFalse(nested.exists())
            nested.mkdir(parents=True)
            _ledger(nested / "ledger.sqlite")

    def test_parent_permission_policy_is_platform_explicit(self):
        database = Path(self.tempdir.name) / "permission-policy" / "ledger.sqlite"
        if os.name == "posix":
            database.parent.mkdir(mode=0o700)
            database.parent.chmod(0o777)
            with self.assertRaises(InvalidInput):
                Ledger(database)
            self.assertFalse(database.exists())
        else:
            with self.assertRaises(InvalidInput):
                Ledger(database)
            self.assertFalse(database.exists())
            database.parent.mkdir()
            acknowledged = Ledger(database, allow_external_acl=True)
            self.assertEqual(
                acknowledged.storage_info()["permission_model"], "external_acl"
            )

    def test_main_database_hardlink_is_rejected(self):
        target = Path(self.tempdir.name) / "unrelated.sqlite"
        target.write_bytes(b"synthetic sentinel")
        alias = Path(self.tempdir.name) / "alias.sqlite"
        os.link(str(target), str(alias))

        with mock.patch("task_state_guard.store.os.open") as open_database:
            with self.assertRaises(InvalidInput):
                _ledger(alias)
        open_database.assert_not_called()

        self.assertEqual(target.read_bytes(), b"synthetic sentinel")
        self.assertEqual(target.stat().st_nlink, 2)

    def test_nonregular_database_is_rejected_before_open(self):
        database = Path(self.tempdir.name) / "directory.sqlite"
        database.mkdir()

        with mock.patch("task_state_guard.store.os.open") as open_database:
            with self.assertRaises(InvalidInput):
                _ledger(database)
        open_database.assert_not_called()

    def test_database_path_replacement_is_detected_before_sqlite_reopens(self):
        task = self.ledger.create_task()
        replacement = Path(self.tempdir.name) / "replacement.sqlite"
        other = _ledger(replacement, clock=self.clock)
        other.create_task()

        os.replace(str(replacement), str(self.db))

        with self.assertRaises(StateConflict):
            self.ledger.get_task(task.id)

    def test_sqlite_sidecar_aliases_are_rejected_without_touching_target(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                directory = Path(self.tempdir.name) / suffix[1:]
                directory.mkdir()
                target = directory / "unrelated.bin"
                target.write_bytes(b"synthetic sentinel")
                database = directory / "ledger.sqlite"
                os.link(str(target), str(database) + suffix)

                with self.assertRaises(StateConflict):
                    _ledger(database)

                self.assertEqual(target.read_bytes(), b"synthetic sentinel")
                self.assertEqual(target.stat().st_nlink, 2)

    def test_transient_unlinked_sidecar_metadata_is_not_an_alias(self):
        sidecar = Path(str(self.ledger.path) + "-journal")
        sidecar.write_bytes(b"")
        real_lstat = Path.lstat
        returned_zero_link = False

        def lstat_with_unlink_snapshot(path):
            nonlocal returned_zero_link
            metadata = real_lstat(path)
            if path == sidecar and not returned_zero_link:
                returned_zero_link = True
                values = list(metadata)
                values[3] = 0
                return os.stat_result(values)
            return metadata

        with mock.patch.object(Path, "lstat", lstat_with_unlink_snapshot):
            self.ledger._secure_sidecar_paths()
        self.assertTrue(returned_zero_link)

    def test_cli_rejected_values_and_path_errors_are_value_free_json(self):
        marker = "invalid" + uuid.uuid4().hex
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(
                _platform_cli_arguments(
                    [
                        "--db",
                        str(Path(self.tempdir.name) / "other.sqlite"),
                        "create",
                        "--timeout-seconds",
                        marker,
                    ]
                )
            )
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(marker, stdout.getvalue())
        self.assertFalse(json.loads(stdout.getvalue())["ok"])

        path_marker = "path" + uuid.uuid4().hex
        blocker = Path(self.tempdir.name) / path_marker
        blocker.write_text("x", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(
                _platform_cli_arguments(
                    ["--db", str(blocker / "ledger.sqlite"), "init"]
                )
            )
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(path_marker, stdout.getvalue())
        self.assertFalse(json.loads(stdout.getvalue())["ok"])

    def test_cli_rejected_sensitive_values_never_reach_streams_or_sqlite(self):
        task = self.ledger.create_task()
        marker = "synthetic_private_" + uuid.uuid4().hex
        cases = (
            ["--db", str(self.db), "create", "--payload-sha256", marker],
            ["--db", str(self.db), "create", "--task-id", marker],
            ["--db", str(self.db), "start", marker],
            [
                "--db",
                str(self.db),
                "close",
                task.id,
                "--status",
                "failed",
                "--code",
                marker,
            ],
            ["--db", str(self.db), "close", task.id, "--status", marker],
        )
        for arguments in cases:
            with self.subTest(command=arguments[2]):
                code, stdout, stderr = self._run_cli(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "")
                self.assertNotIn(marker, stdout)
                self.assertEqual(stdout, '{"error":"InvalidInput","ok":false}\n')

        retained = self.ledger.get_task(task.id)
        self.assertEqual(retained.status, TaskStatus.QUEUED)
        self.assertEqual(self.ledger.doctor()["task_count"], 1)
        self._assert_marker_absent_from_storage(marker)

    def test_cli_internal_exception_is_fixed_and_value_free(self):
        marker = "synthetic_private_" + uuid.uuid4().hex
        with mock.patch(
            "task_state_guard.cli.Ledger", side_effect=RuntimeError(marker)
        ):
            code, stdout, stderr = self._run_cli(
                ["--db", str(self.db), "doctor"]
            )
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "")
        self.assertNotIn(marker, stdout)
        self.assertEqual(stdout, '{"error":"InternalError","ok":false}\n')

    def test_module_cli_accepts_unicode_and_space_database_path(self):
        database = Path(self.tempdir.name) / "state 空格" / "任务.sqlite"
        if os.name == "nt":
            # External ACL mode deliberately requires a pre-created parent;
            # acknowledgement must not silently weaken that production boundary.
            database.parent.mkdir()
        command = [
            sys.executable,
            "-m",
            "task_state_guard",
            *_platform_cli_arguments(["--db", str(database), "init"]),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["storage"]["permission_model"],
            "posix_mode" if os.name == "posix" else "external_acl",
        )
        self.assertTrue(_ledger(database).doctor()["healthy"])

    def test_module_version_is_stable(self):
        module = subprocess.run(
            [sys.executable, "-m", "task_state_guard", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(module.returncode, 0)
        self.assertEqual(module.stderr, "")
        self.assertEqual(module.stdout, "task-state-guard 0.1.0\n")

    def test_schema_spoof_is_rejected_and_v1_migrates(self):
        spoofed = Path(self.tempdir.name) / "spoofed.sqlite"
        with _sqlite_connection(spoofed) as connection:
            connection.execute(
                "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_meta VALUES ('schema_version', '1')"
            )
            connection.execute(
                "CREATE TABLE tasks("
                "id TEXT PRIMARY KEY, parent_id TEXT, status TEXT, "
                "delivery_status TEXT, created_at REAL, started_at REAL, "
                "updated_at REAL, ended_at REAL, deadline_at REAL)"
            )
        with self.assertRaises(StateConflict):
            _ledger(spoofed)

        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
            )
            connection.execute(
                "DELETE FROM schema_meta WHERE key = 'schema_fingerprint'"
            )
        migrated = _ledger(self.db, clock=self.clock)
        report = migrated.doctor()
        self.assertTrue(report["healthy"])
        self.assertEqual(report["schema_version"], Ledger.SCHEMA_VERSION)

    def test_schema_fingerprint_preserves_string_literal_case(self):
        with _sqlite_connection(self.db) as connection:
            rows = connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
            ).fetchall()
            metadata = connection.execute(
                "SELECT key, value FROM schema_meta"
            ).fetchall()
        spoofed = Path(self.tempdir.name) / "case-spoofed.sqlite"
        with _sqlite_connection(spoofed) as connection:
            for object_type in ("table", "index"):
                for row_type, name, sql in rows:
                    if row_type != object_type:
                        continue
                    if name == "tasks":
                        sql = sql.replace("'queued'", "'QUEUED'")
                    connection.execute(sql)
            connection.executemany(
                "INSERT INTO schema_meta(key, value) VALUES (?, ?)", metadata
            )
        with self.assertRaises(StateConflict):
            _ledger(spoofed)

    def test_v1_migration_rejects_unregistered_reason_metadata(self):
        task = self.ledger.create_task()
        self.ledger.close_task(task.id, "failed", code="worker_failed")
        marker = "reason_" + uuid.uuid4().hex
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET terminal_code = ? WHERE id = ?", (marker, task.id)
            )
            connection.execute(
                "UPDATE task_events SET code = ? "
                "WHERE task_id = ? AND event_kind = 'task_transition'",
                (marker, task.id),
            )
            connection.execute(
                "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
            )
            connection.execute(
                "DELETE FROM schema_meta WHERE key = 'schema_fingerprint'"
            )

        with self.assertRaises(StateConflict) as caught:
            _ledger(self.db)
        self.assertNotIn(marker, str(caught.exception))
        with _sqlite_connection(self.db) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            fingerprint_count = connection.execute(
                "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_fingerprint'"
            ).fetchone()[0]
        self.assertEqual(version, "1")
        self.assertEqual(fingerprint_count, 0)

    def test_doctor_detects_schema_and_event_tampering(self):
        task = self.ledger.create_task()
        self.ledger.start_task(task.id)
        self.ledger.close_task(task.id, "succeeded")
        with _sqlite_connection(self.db) as connection:
            connection.execute("DELETE FROM task_events")
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["event_inconsistency_count"], 1)

        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'"
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertFalse(report["schema_ok"])
        self.assertEqual(report["schema_version"], 7)

    def test_extra_schema_metadata_is_rejected_without_value_disclosure(self):
        marker = "synthetic_private_" + uuid.uuid4().hex
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
                ("unexpected_metadata", marker),
            )

        report = self.ledger.doctor()
        rendered = json.dumps(report, sort_keys=True)
        self.assertFalse(report["healthy"])
        self.assertFalse(report["schema_ok"])
        self.assertEqual(report["doctor_error_count"], 1)
        self.assertNotIn(marker, rendered)

        with self.assertRaises(StateConflict) as caught:
            _ledger(self.db)
        self.assertNotIn(marker, str(caught.exception))

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(
                _platform_cli_arguments(["--db", str(self.db), "doctor"])
            )
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(marker, stdout.getvalue())

    def test_doctor_validates_exact_event_content(self):
        task = self.ledger.create_task()
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE task_events SET from_value = 'running', to_value = 'failed', "
                "occurred_at = ? WHERE task_id = ?",
                (self.clock.value - 1, task.id),
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["event_inconsistency_count"], 1)

    def test_doctor_detects_delivery_semantic_tampering(self):
        task = self.ledger.create_task(delivery_required=True)
        self.ledger.close_task(task.id, "succeeded")
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET delivery_status = 'not_applicable', "
                "delivery_updated_at = ended_at WHERE id = ?",
                (task.id,),
            )
            connection.execute(
                "INSERT INTO task_events(task_id, event_kind, from_value, to_value, "
                "occurred_at, code) VALUES (?, 'delivery_transition', 'pending', "
                "'not_applicable', ?, NULL)",
                (task.id, self.clock.value),
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["state_inconsistency_count"], 1)

    def test_parent_child_time_cannot_move_backwards_and_doctor_checks_it(self):
        parent = _ledger(self.db, clock=lambda: 2_000.0).create_task()
        earlier = _ledger(self.db, clock=lambda: 1_000.0)
        with self.assertRaises(StateConflict):
            earlier.create_task(parent_id=parent.id)

        later = _ledger(self.db, clock=lambda: 2_001.0)
        child = later.create_task(parent_id=parent.id)
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET created_at = 1999, updated_at = 1999, "
                "delivery_updated_at = 1999 WHERE id = ?",
                (child.id,),
            )
            connection.execute(
                "UPDATE task_events SET occurred_at = 1999 WHERE task_id = ?",
                (child.id,),
            )
        report = later.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["timestamp_inconsistency_count"], 1)

    def test_doctor_detects_parent_cycles(self):
        first = self.ledger.create_task()
        second = self.ledger.create_task()
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET parent_id = ? WHERE id = ?", (second.id, first.id)
            )
            connection.execute(
                "UPDATE tasks SET parent_id = ? WHERE id = ?", (first.id, second.id)
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["parent_cycle_count"], 1)

    def test_doctor_rejects_noncanonical_stored_uuid(self):
        synthetic_hex = ("a" * 12) + "4" + ("a" * 3) + "8" + ("a" * 15)
        task = self.ledger.create_task(task_id=str(uuid.UUID(hex=synthetic_hex)))
        noncanonical = task.id.upper()
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET id = ? WHERE id = ?", (noncanonical, task.id)
            )
            connection.execute(
                "UPDATE task_events SET task_id = ? WHERE task_id = ?",
                (noncanonical, task.id),
            )
        report = self.ledger.doctor()
        self.assertFalse(report["healthy"])
        self.assertEqual(report["state_inconsistency_count"], 1)

    def test_backward_clock_and_deadline_overflow_are_rejected(self):
        later = _ledger(self.db, clock=lambda: 2_000.0)
        task = later.create_task()
        earlier = _ledger(self.db, clock=lambda: 1_000.0)
        with self.assertRaises(StateConflict):
            earlier.start_task(task.id)

        huge_db = Path(self.tempdir.name) / "huge.sqlite"
        huge = _ledger(huge_db, clock=lambda: 1e308)
        huge_task = huge.create_task(timeout_seconds=1e308)
        with self.assertRaises(InvalidInput):
            huge.start_task(huge_task.id)

    def test_delivery_required_semantics_are_enforced(self):
        external = self.ledger.create_task(delivery_required=True)
        self.ledger.close_task(external.id, "succeeded")
        with self.assertRaises(StateConflict):
            self.ledger.set_delivery(external.id, DeliveryStatus.NOT_APPLICABLE)

        internal = self.ledger.create_task(delivery_required=False)
        self.ledger.close_task(internal.id, "succeeded")
        with self.assertRaises(StateConflict):
            self.ledger.set_delivery(internal.id, DeliveryStatus.DELIVERED)

    def test_unregistered_reason_is_not_stored_or_emitted(self):
        marker = "reason_" + uuid.uuid4().hex
        task = self.ledger.create_task()
        with self.assertRaises(InvalidInput):
            self.ledger.close_task(task.id, "failed", code=marker)
        with _sqlite_connection(self.db) as connection:
            stored = connection.execute(
                "SELECT COUNT(*) FROM task_events WHERE code = ?", (marker,)
            ).fetchone()[0]
            stored += connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE terminal_code = ?", (marker,)
            ).fetchone()[0]
        self.assertEqual(stored, 0)
        self.assertNotIn(marker, json.dumps(self.ledger.get_task(task.id).to_dict()))

    def test_reason_codes_must_match_the_terminal_state(self):
        task = self.ledger.create_task()
        with self.assertRaises(InvalidInput):
            self.ledger.close_task(task.id, "failed", code="worker_completed")
        self.ledger.close_task(task.id, "failed", code="worker_failed")
        with self.assertRaises(InvalidInput):
            self.ledger.set_delivery(
                task.id, "failed", code="transport_acknowledged"
            )
        delivered = self.ledger.set_delivery(
            task.id, "failed", code="transport_failed"
        )
        self.assertEqual(delivered.delivery_code, "transport_failed")

    def test_tampered_reason_is_not_returned_by_api_or_cli(self):
        task = self.ledger.create_task()
        self.ledger.close_task(task.id, "failed", code="worker_failed")
        marker = "reason_" + uuid.uuid4().hex
        with _sqlite_connection(self.db) as connection:
            connection.execute(
                "UPDATE tasks SET terminal_code = ? WHERE id = ?", (marker, task.id)
            )
            connection.execute(
                "UPDATE task_events SET code = ? WHERE task_id = ? "
                "AND event_kind = 'task_transition'",
                (marker, task.id),
            )
        with self.assertRaises(StateConflict) as caught:
            self.ledger.get_task(task.id)
        self.assertNotIn(marker, str(caught.exception))

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(
                _platform_cli_arguments(
                    ["--db", str(self.db), "heartbeat", task.id]
                )
            )
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(marker, stdout.getvalue())
        self.assertFalse(self.ledger.doctor()["healthy"])

    def test_payload_hash_is_hidden_from_default_and_cli_output(self):
        digest = payload_fingerprint("runtime-generated-low-entropy-example")
        task = self.ledger.create_task(payload_hash=digest)
        self.assertNotIn(digest, json.dumps(task.to_dict()))
        self.assertIn(digest, json.dumps(task.to_dict(include_payload_hash=True)))

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cli_main(
                _platform_cli_arguments(
                    [
                        "--db",
                        str(Path(self.tempdir.name) / "cli.sqlite"),
                        "create",
                        "--payload-sha256",
                        digest,
                    ]
                )
            )
        self.assertEqual(result, 0)
        self.assertNotIn(digest, stdout.getvalue())


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--ledger-worker":
        raise SystemExit(_multiprocess_writer(sys.argv[2], int(sys.argv[3])))
    unittest.main()
