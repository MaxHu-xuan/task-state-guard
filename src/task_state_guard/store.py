# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed task and delivery state machines."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

from .model import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_DELIVERY_STATUSES,
    TERMINAL_TASK_STATUSES,
    DeliveryStatus,
    InvalidInput,
    StateConflict,
    Task,
    TaskNotFound,
    TaskStatus,
    coerce_delivery_status,
    coerce_task_status,
    validate_delivery_code,
    validate_nonnegative_number,
    validate_payload_hash,
    validate_task_code,
    validate_uuid,
)


Clock = Callable[[], float]
_POSIX_MODE_SECURITY = os.name == "posix"
_WINDOWS_PLATFORM = os.name == "nt"
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SNAPSHOT_WIPE_ZEROES = b"\x00" * (64 * 1024)


def _wipe_bytearray(snapshot: Optional[bytearray]) -> None:
    if snapshot is None:
        return
    chunk_size = len(_SNAPSHOT_WIPE_ZEROES)
    for offset in range(0, len(snapshot), chunk_size):
        width = min(chunk_size, len(snapshot) - offset)
        snapshot[offset : offset + width] = _SNAPSHOT_WIPE_ZEROES[:width]
    snapshot.clear()


class _SnapshotConnection(sqlite3.Connection):
    """Keep deserialized bytes alive, then wipe them after SQLite closes."""

    def retain_snapshot(self, snapshot: bytearray) -> None:
        self._snapshot_buffer = snapshot

    def close(self) -> None:
        snapshot = getattr(self, "_snapshot_buffer", None)
        try:
            super().close()
        finally:
            _wipe_bytearray(snapshot)
            self._snapshot_buffer = None


def _is_link_like(metadata: os.stat_result) -> bool:
    """Recognize POSIX symlinks and Windows reparse-point aliases."""

    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or (
        isinstance(file_attributes, int)
        and bool(file_attributes & _WINDOWS_REPARSE_POINT)
    )


class Ledger:
    """A file-backed SQLite ledger with one connection per operation."""

    SCHEMA_VERSION = 2
    _LEGACY_SCHEMA_VERSION = 1
    _READ_ONLY_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
    _SNAPSHOT_CHUNK_BYTES = 1024 * 1024
    _SCHEMA_OBJECTS = frozenset(
        (
            "schema_meta",
            "task_events",
            "tasks",
            "tasks_delivery_idx",
            "tasks_parent_idx",
            "tasks_status_idx",
        )
    )
    _SCHEMA_FINGERPRINT = "50ae209ec97afb360e142cfae85767f575acc2a065103f5b642bfd0c141e08f6"

    def __init__(
        self,
        path: Union[str, os.PathLike],
        *,
        clock: Clock = time.time,
        busy_timeout_ms: int = 5000,
        allow_external_acl: bool = False,
        read_only: bool = False,
    ) -> None:
        try:
            raw_path = os.fspath(path)
        except TypeError:
            raise InvalidInput("database path must be path-like") from None
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise InvalidInput("database path must be a nonempty text path")
        if raw_path == ":memory:":
            raise InvalidInput("use a file-backed database so connections share state")
        if _WINDOWS_PLATFORM and raw_path.replace("/", "\\").startswith("\\\\"):
            raise InvalidInput("network database paths are unsupported")
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms < 0
        ):
            raise InvalidInput("busy_timeout_ms must be nonnegative")
        if not callable(clock):
            raise InvalidInput("clock must be callable")
        if not isinstance(allow_external_acl, bool):
            raise InvalidInput("allow_external_acl must be bool")
        if not isinstance(read_only, bool):
            raise InvalidInput("read_only must be bool")
        if not _POSIX_MODE_SECURITY and not allow_external_acl:
            raise InvalidInput(
                "platform ACLs cannot be verified; explicit acknowledgement required"
            )
        lexical_path = Path(os.path.abspath(raw_path))
        try:
            canonical_parent = lexical_path.parent.resolve(strict=False)
        except OSError:
            raise InvalidInput("database parent is unavailable") from None
        # Canonicalizing the parent gives subsequent identity checks one stable
        # pathname while still refusing aliases at the database filename itself.
        self.path = canonical_parent / lexical_path.name
        self._clock = clock
        self._busy_timeout_ms = busy_timeout_ms
        self._read_only = read_only
        self._permission_model = (
            "posix_mode" if _POSIX_MODE_SECURITY else "external_acl"
        )
        self._file_identity = self._prepare_storage_path(read_only=read_only)
        self._initialize()

    @staticmethod
    def _validate_directory(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError:
            raise InvalidInput("database parent is unavailable") from None
        if _is_link_like(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise InvalidInput("database parent must be a real directory")

    def _prepare_parent(self, *, create_missing: bool = True) -> None:
        parent = self.path.parent
        anchor = Path(parent.anchor)
        self._validate_directory(anchor)
        current = anchor
        relative_parts = parent.parts[1:] if parent.is_absolute() else parent.parts
        for part in relative_parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if not create_missing:
                    raise InvalidInput("database parent is unavailable") from None
                if not _POSIX_MODE_SECURITY:
                    raise InvalidInput(
                        "database parent must be pre-created under external ACL"
                    ) from None
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                except OSError:
                    raise InvalidInput("database parent cannot be created") from None
                self._validate_directory(current)
                continue
            except OSError:
                raise InvalidInput("database parent is unavailable") from None
            if _is_link_like(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise InvalidInput("database parent must not contain symlinks")
        self._validate_parent_security()

    def _validate_parent_security(self) -> None:
        """Require a private owner-controlled leaf and safe ancestor renames."""

        if not _POSIX_MODE_SECURITY:
            # Python's standard library cannot inspect or set a Windows DACL
            # with semantics equivalent to owner-only POSIX modes.  The caller
            # has explicitly acknowledged that this boundary is enforced
            # outside TaskStateGuard.
            return

        try:
            leaf = self.path.parent.lstat()
        except OSError:
            raise InvalidInput("database parent is unavailable") from None
        if hasattr(os, "geteuid") and leaf.st_uid != os.geteuid():
            raise InvalidInput("database parent must be owned by the current user")
        if leaf.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise InvalidInput("database parent must not be group/world writable")

        current = self.path.parent
        while True:
            try:
                metadata = current.lstat()
            except OSError:
                raise InvalidInput("database ancestor is unavailable") from None
            writable_by_others = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            sticky = metadata.st_mode & stat.S_ISVTX
            if writable_by_others and not sticky:
                raise InvalidInput("database ancestor permits unsafe replacement")
            if current == current.parent:
                break
            current = current.parent

    def _prepare_storage_path(self, *, read_only: bool) -> Tuple[int, int]:
        self._prepare_parent(create_missing=not read_only)
        try:
            existing = self.path.lstat()
        except FileNotFoundError:
            if read_only:
                raise InvalidInput("read-only database must already exist") from None
        except OSError:
            raise InvalidInput("database file cannot be inspected safely") from None
        else:
            if (
                _is_link_like(existing)
                or not stat.S_ISREG(existing.st_mode)
                or existing.st_nlink != 1
            ):
                raise InvalidInput(
                    "database file must be a single-link regular file"
                )
        flags = os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOINHERIT"):
            flags |= os.O_NOINHERIT
        try:
            descriptor = os.open(str(self.path), flags, 0o600)
        except OSError:
            raise InvalidInput("database file cannot be opened safely") from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise InvalidInput("database file must be a single-link regular file")
            if not metadata.st_ino:
                raise InvalidInput("database file identity is unavailable")
            if _POSIX_MODE_SECURITY and read_only:
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise InvalidInput(
                        "read-only database permissions must already be private"
                    )
            elif _POSIX_MODE_SECURITY:
                try:
                    os.fchmod(descriptor, 0o600)
                except (AttributeError, OSError):
                    raise InvalidInput(
                        "database permissions cannot be secured"
                    ) from None
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    raise InvalidInput("database permissions cannot be secured")
            return metadata.st_dev, metadata.st_ino
        finally:
            os.close(descriptor)

    def _secure_sidecar_paths(self, *, tighten_permissions: bool = True) -> None:
        """Reject aliases and tighten permissions on existing SQLite sidecars."""

        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            try:
                metadata = sidecar.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise StateConflict("SQLite sidecar cannot be inspected safely") from None
            if _is_link_like(metadata):
                raise StateConflict("SQLite sidecar must not be a symbolic link")
            if not stat.S_ISREG(metadata.st_mode):
                raise StateConflict("SQLite sidecar must be a regular file")
            if metadata.st_nlink == 0:
                # On local filesystems lstat can race SQLite unlinking a
                # transient WAL/SHM object and return its final zero-link
                # metadata snapshot.  The pathname no longer aliases that
                # inode, so there is nothing to secure in this pass.
                continue
            if metadata.st_nlink != 1:
                raise StateConflict("SQLite sidecar must not have multiple links")
            if (
                _POSIX_MODE_SECURITY
                and stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                if not tighten_permissions:
                    raise StateConflict(
                        "read-only SQLite sidecar permissions must already be private"
                    )
                try:
                    os.chmod(str(sidecar), 0o600, follow_symlinks=False)
                except FileNotFoundError:
                    # SQLite may remove a transient sidecar between lstat/chmod.
                    continue
                except (NotImplementedError, OSError):
                    raise StateConflict(
                        "SQLite sidecar permissions cannot be secured"
                    ) from None

    def _verify_storage_path(self) -> None:
        self._prepare_parent(create_missing=False)
        try:
            metadata = self.path.lstat()
        except OSError:
            raise StateConflict("database file identity changed") from None
        identity = (metadata.st_dev, metadata.st_ino)
        if (
            _is_link_like(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or identity != self._file_identity
        ):
            raise StateConflict("database file identity changed")

    def _read_only_has_wal_pair(self) -> bool:
        """Return whether a complete WAL/SHM pair exists, failing on partial state."""

        presence = {}
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).lstat()
                presence[suffix] = True
            except FileNotFoundError:
                presence[suffix] = False
            except OSError:
                raise StateConflict("SQLite sidecar cannot be inspected safely") from None
        journal_exists = presence["-journal"]
        wal_exists = presence["-wal"]
        shm_exists = presence["-shm"]
        if journal_exists or wal_exists != shm_exists:
            raise StateConflict("read-only SQLite sidecar state is incomplete")
        return wal_exists

    @staticmethod
    def _snapshot_signature(metadata: os.stat_result) -> Tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_nlink,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def _read_snapshot_pass(
        self, *, capture: bool
    ) -> Tuple[Tuple[int, ...], bytes, bytes, Optional[bytearray]]:
        """Hash or capture one no-sidecar source pass under identity checks."""

        self._verify_storage_path()
        if self._read_only_has_wal_pair():
            raise StateConflict("read-only snapshot requires a closed WAL database")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOINHERIT"):
            flags |= os.O_NOINHERIT
        try:
            descriptor = os.open(str(self.path), flags)
        except OSError:
            raise StateConflict("database snapshot cannot be read safely") from None
        collected = bytearray() if capture else None
        digest = hashlib.sha256()
        header = bytearray()
        total = 0
        try:
            before = os.fstat(descriptor)
            before_signature = self._snapshot_signature(before)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or (before.st_dev, before.st_ino) != self._file_identity
            ):
                raise StateConflict("database file identity changed")
            if before.st_size > self._READ_ONLY_SNAPSHOT_MAX_BYTES:
                raise StateConflict("read-only database exceeds snapshot limit")
            while True:
                chunk = os.read(descriptor, self._SNAPSHOT_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > self._READ_ONLY_SNAPSHOT_MAX_BYTES:
                    raise StateConflict("read-only database exceeds snapshot limit")
                digest.update(chunk)
                if len(header) < 20:
                    header.extend(chunk[: 20 - len(header)])
                if collected is not None:
                    collected.extend(chunk)
            after = os.fstat(descriptor)
            if (
                self._snapshot_signature(after) != before_signature
                or total != before.st_size
            ):
                raise StateConflict("database changed during read-only snapshot")
        except OSError:
            _wipe_bytearray(collected)
            raise StateConflict("database snapshot cannot be read safely") from None
        except MemoryError:
            _wipe_bytearray(collected)
            raise StateConflict("read-only database snapshot is unavailable") from None
        except BaseException:
            _wipe_bytearray(collected)
            raise
        finally:
            os.close(descriptor)
        try:
            self._verify_storage_path()
            if self._read_only_has_wal_pair():
                raise StateConflict("database changed during read-only snapshot")
        except BaseException:
            _wipe_bytearray(collected)
            raise
        return before_signature, digest.digest(), bytes(header), collected

    def _stable_database_snapshot(self) -> bytearray:
        """Return stable source bytes only after two matching, sidecar-free passes."""

        snapshot = None
        try:
            first_signature, first_digest, first_header, _unused = (
                self._read_snapshot_pass(capture=False)
            )
            second_signature, second_digest, second_header, snapshot = (
                self._read_snapshot_pass(capture=True)
            )
            if (
                snapshot is None
                or first_signature != second_signature
                or first_digest != second_digest
                or first_header != second_header
            ):
                raise StateConflict("database changed during read-only snapshot")
            if (
                second_header[:16] != b"SQLite format 3\x00"
                or second_header[18:20] != b"\x02\x02"
            ):
                raise StateConflict("read-only database requires WAL mode")
            # A sidecar-free WAL database is fully checkpointed, but SQLite
            # cannot deserialize WAL-format header bytes into an in-memory
            # database. Normalize only the private snapshot copy to rollback
            # format; the verified source bytes remain untouched.
            snapshot[18:20] = b"\x01\x01"
            return snapshot
        except BaseException:
            _wipe_bytearray(snapshot)
            raise

    @staticmethod
    def _deserialize_database_snapshot(
        connection: sqlite3.Connection, snapshot: bytearray
    ) -> None:
        deserialize = getattr(connection, "deserialize", None)
        if not callable(deserialize):
            raise StateConflict("SQLite snapshot deserialization is unavailable")
        try:
            deserialize(snapshot)
        except (BufferError, MemoryError, sqlite3.Error, TypeError, ValueError):
            raise StateConflict("SQLite snapshot deserialization failed") from None

    def _connect_stable_snapshot(self) -> sqlite3.Connection:
        snapshot = self._stable_database_snapshot()
        connection = None
        try:
            connection = sqlite3.connect(
                ":memory:",
                isolation_level=None,
                factory=_SnapshotConnection,
            )
            self._deserialize_database_snapshot(connection, snapshot)
            connection.retain_snapshot(snapshot)
            snapshot = None
            return connection
        except MemoryError:
            if connection is not None:
                connection.close()
            raise StateConflict("read-only database snapshot is unavailable") from None
        except sqlite3.Error:
            if connection is not None:
                connection.close()
            raise StateConflict("database connection failed") from None
        except BaseException:
            if connection is not None:
                connection.close()
            raise
        finally:
            _wipe_bytearray(snapshot)

    def _now(self) -> float:
        try:
            value = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            raise InvalidInput("clock returned an invalid timestamp") from None
        if not math.isfinite(value):
            raise InvalidInput("clock returned a non-finite timestamp")
        return value

    def _connect(self, *, set_wal: bool = False) -> sqlite3.Connection:
        if self._read_only and set_wal:
            raise StateConflict("read-only ledger cannot change journal mode")
        self._verify_storage_path()
        self._secure_sidecar_paths(tighten_permissions=not self._read_only)
        try:
            if self._read_only and not self._read_only_has_wal_pair():
                connection = self._connect_stable_snapshot()
            else:
                target = (
                    self.path.as_uri() + "?mode=ro"
                    if self._read_only
                    else str(self.path)
                )
                connection = sqlite3.connect(
                    target,
                    timeout=max(self._busy_timeout_ms / 1000.0, 0.001),
                    isolation_level=None,
                    uri=self._read_only,
                )
        except sqlite3.Error:
            raise StateConflict("database connection failed") from None
        try:
            self._verify_storage_path()
            self._secure_sidecar_paths(tighten_permissions=not self._read_only)
        except BaseException:
            connection.close()
            raise
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = %d" % self._busy_timeout_ms)
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise StateConflict("SQLite foreign-key enforcement is unavailable")
            if set_wal:
                journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(journal_mode).lower() != "wal":
                    raise StateConflict("SQLite WAL mode is unavailable")
            if not self._read_only:
                connection.execute("PRAGMA synchronous = NORMAL")
            self._secure_sidecar_paths(tighten_permissions=not self._read_only)
            return connection
        except sqlite3.Error:
            connection.close()
            raise StateConflict("database configuration failed") from None
        except BaseException:
            connection.close()
            raise

    @classmethod
    def _schema_signature(cls, connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()
        normalized = [
            [row["type"], row["name"], row["tbl_name"], " ".join(row["sql"].split())]
            for row in rows
        ]
        encoded = json.dumps(
            normalized, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _schema_objects(cls, connection: sqlite3.Connection) -> frozenset:
        return frozenset(
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
            ).fetchall()
        )

    @classmethod
    def _schema_metadata_state(
        cls, connection: sqlite3.Connection
    ) -> Tuple[Optional[int], bool]:
        """Return the bounded version and whether metadata is an exact known set."""

        total = connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
        unknown = connection.execute(
            "SELECT COUNT(*) FROM schema_meta "
            "WHERE key NOT IN ('schema_version', 'schema_fingerprint')"
        ).fetchone()[0]
        version_row = connection.execute(
            "SELECT value FROM schema_meta "
            "WHERE key = 'schema_version' "
            "AND typeof(value) = 'text' AND length(value) <= 20"
        ).fetchone()
        raw_version = version_row["value"] if version_row is not None else None
        actual_version = (
            int(raw_version)
            if isinstance(raw_version, str) and raw_version.isdigit()
            else None
        )
        fingerprint_ok = bool(
            connection.execute(
                "SELECT 1 FROM schema_meta "
                "WHERE key = 'schema_fingerprint' AND value = ?",
                (cls._SCHEMA_FINGERPRINT,),
            ).fetchone()
        )
        if actual_version == cls._LEGACY_SCHEMA_VERSION:
            return actual_version, total == 1 and unknown == 0 and not fingerprint_ok
        if actual_version == cls.SCHEMA_VERSION:
            return actual_version, total == 2 and unknown == 0 and fingerprint_ok
        return actual_version, False

    @classmethod
    def _validate_existing_schema(cls, connection: sqlite3.Connection) -> None:
        if cls._schema_objects(connection) != cls._SCHEMA_OBJECTS:
            raise StateConflict("database schema does not match TaskStateGuard")
        if cls._schema_signature(connection) != cls._SCHEMA_FINGERPRINT:
            raise StateConflict("database schema fingerprint mismatch")
        actual_version, metadata_ok = cls._schema_metadata_state(connection)
        if actual_version not in (cls._LEGACY_SCHEMA_VERSION, cls.SCHEMA_VERSION):
            raise StateConflict("unsupported schema version")
        if not metadata_ok:
            raise StateConflict("database schema metadata mismatch")

    @staticmethod
    def _legacy_reason_codes_are_safe(connection: sqlite3.Connection) -> bool:
        """Return false without echoing any legacy free-form reason value."""

        try:
            for row in connection.execute(
                "SELECT status, delivery_status, terminal_code, delivery_code FROM tasks"
            ):
                task_status = coerce_task_status(row["status"])
                delivery_status = coerce_delivery_status(row["delivery_status"])
                if task_status in TERMINAL_TASK_STATUSES:
                    validate_task_code(row["terminal_code"], task_status)
                elif row["terminal_code"] is not None:
                    return False
                if delivery_status in TERMINAL_DELIVERY_STATUSES:
                    validate_delivery_code(row["delivery_code"], delivery_status)
                elif row["delivery_code"] is not None:
                    return False

            for row in connection.execute(
                "SELECT event_kind, to_value, code FROM task_events"
            ):
                if row["event_kind"] == "created":
                    if row["to_value"] != "queued" or row["code"] != "created":
                        return False
                elif row["event_kind"] == "task_transition":
                    if row["to_value"] == "running":
                        if row["code"] != "started":
                            return False
                    else:
                        validate_task_code(
                            row["code"], coerce_task_status(row["to_value"])
                        )
                elif row["event_kind"] == "delivery_transition":
                    validate_delivery_code(
                        row["code"], coerce_delivery_status(row["to_value"])
                    )
                else:
                    return False
        except (InvalidInput, TypeError, ValueError):
            return False
        return True

    @contextmanager
    def _transaction(
        self, *, read_only: bool = False
    ) -> Iterator[sqlite3.Connection]:
        if self._read_only and not read_only:
            raise StateConflict("ledger is read-only")
        connection = self._connect()
        try:
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        if self._read_only:
            connection = self._connect()
            try:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if str(journal_mode).lower() not in ("memory", "wal"):
                    raise StateConflict("read-only database requires WAL mode")
                if not self._schema_objects(connection):
                    raise StateConflict("read-only database is not initialized")
                self._validate_existing_schema(connection)
                actual_version, _metadata_ok = self._schema_metadata_state(connection)
                if (
                    actual_version == self._LEGACY_SCHEMA_VERSION
                    and not self._legacy_reason_codes_are_safe(connection)
                ):
                    raise StateConflict(
                        "legacy database contains unsafe reason metadata"
                    )
            finally:
                connection.close()
            return

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_objects = self._schema_objects(connection)
            if existing_objects:
                self._validate_existing_schema(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT REFERENCES tasks(id),
                    status TEXT NOT NULL CHECK (
                        status IN ('queued','running','succeeded','failed','timed_out','cancelled')
                    ),
                    delivery_status TEXT NOT NULL CHECK (
                        delivery_status IN ('pending','delivered','failed','not_applicable')
                    ),
                    delivery_required INTEGER NOT NULL CHECK (delivery_required IN (0,1)),
                    payload_hash TEXT CHECK (
                        payload_hash IS NULL OR (
                            length(payload_hash) = 64 AND
                            payload_hash NOT GLOB '*[^0-9a-f]*'
                        )
                    ),
                    timeout_seconds REAL CHECK (timeout_seconds IS NULL OR timeout_seconds >= 0),
                    created_at REAL NOT NULL,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    ended_at REAL,
                    deadline_at REAL,
                    delivery_updated_at REAL NOT NULL,
                    terminal_code TEXT,
                    delivery_code TEXT,
                    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                    CHECK (parent_id IS NULL OR parent_id <> id),
                    CHECK (deadline_at IS NULL OR started_at IS NOT NULL),
                    CHECK (
                        (status IN ('queued','running') AND ended_at IS NULL) OR
                        (status IN ('succeeded','failed','timed_out','cancelled')
                         AND ended_at IS NOT NULL)
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    event_kind TEXT NOT NULL CHECK (
                        event_kind IN ('created','task_transition','delivery_transition')
                    ),
                    from_value TEXT,
                    to_value TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    code TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_parent_idx ON tasks(parent_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status, updated_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_delivery_idx "
                "ON tasks(delivery_status, ended_at)"
            )
            current = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                if self._schema_signature(connection) != self._SCHEMA_FINGERPRINT:
                    raise StateConflict("created database schema fingerprint mismatch")
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),),
                )
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('schema_fingerprint', ?)",
                    (self._SCHEMA_FINGERPRINT,),
                )
            elif current["value"] == str(self._LEGACY_SCHEMA_VERSION):
                if self._schema_signature(connection) != self._SCHEMA_FINGERPRINT:
                    raise StateConflict("database schema fingerprint mismatch")
                if not self._legacy_reason_codes_are_safe(connection):
                    raise StateConflict("legacy database contains unsafe reason metadata")
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(self.SCHEMA_VERSION),),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) "
                    "VALUES ('schema_fingerprint', ?)",
                    (self._SCHEMA_FINGERPRINT,),
                )
            elif current["value"] == str(self.SCHEMA_VERSION):
                fingerprint = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_fingerprint'"
                ).fetchone()
                if (
                    fingerprint is None
                    or fingerprint["value"] != self._SCHEMA_FINGERPRINT
                    or self._schema_signature(connection) != self._SCHEMA_FINGERPRINT
                ):
                    raise StateConflict("database schema fingerprint mismatch")
            else:
                raise StateConflict("unsupported schema version")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        wal_connection = self._connect(set_wal=True)
        wal_connection.close()

    @staticmethod
    def _finite_timestamp(value: Any, *, optional: bool = False) -> Optional[float]:
        if value is None and optional:
            return None
        if isinstance(value, bool):
            raise InvalidInput("stored timestamp is invalid")
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise InvalidInput("stored timestamp is invalid") from None
        if not math.isfinite(result):
            raise InvalidInput("stored timestamp is invalid")
        return result

    @classmethod
    def _task_from_row(cls, row: sqlite3.Row) -> Task:
        """Build a Task only from a row that satisfies public invariants."""

        try:
            identifier = validate_uuid(row["id"])
            if identifier != row["id"]:
                raise InvalidInput("stored task id is not canonical")
            parent = (
                validate_uuid(row["parent_id"])
                if row["parent_id"] is not None
                else None
            )
            if parent is not None and parent != row["parent_id"]:
                raise InvalidInput("stored parent id is not canonical")
            status = coerce_task_status(row["status"])
            delivery_status = coerce_delivery_status(row["delivery_status"])
            if row["delivery_required"] not in (0, 1):
                raise InvalidInput("stored delivery flag is invalid")
            delivery_required = bool(row["delivery_required"])
            payload_hash = validate_payload_hash(row["payload_hash"])
            timeout = (
                validate_nonnegative_number(row["timeout_seconds"], "timeout_seconds")
                if row["timeout_seconds"] is not None
                else None
            )
            created = cls._finite_timestamp(row["created_at"])
            started = cls._finite_timestamp(row["started_at"], optional=True)
            updated = cls._finite_timestamp(row["updated_at"])
            ended = cls._finite_timestamp(row["ended_at"], optional=True)
            deadline = cls._finite_timestamp(row["deadline_at"], optional=True)
            delivery_updated = cls._finite_timestamp(row["delivery_updated_at"])
            if isinstance(row["version"], bool):
                raise InvalidInput("stored version is invalid")
            version = int(row["version"])
            if version < 0 or version != row["version"]:
                raise InvalidInput("stored version is invalid")

            terminal_code = row["terminal_code"]
            if status in TERMINAL_TASK_STATUSES:
                terminal_code = validate_task_code(terminal_code, status)
            elif terminal_code is not None:
                raise InvalidInput("active task has terminal metadata")

            delivery_code = row["delivery_code"]
            if delivery_status in TERMINAL_DELIVERY_STATUSES:
                delivery_code = validate_delivery_code(delivery_code, delivery_status)
            elif delivery_code is not None:
                raise InvalidInput("pending delivery has terminal metadata")

            if delivery_required and delivery_status is DeliveryStatus.NOT_APPLICABLE:
                raise InvalidInput("stored delivery semantics are invalid")
            if not delivery_required and delivery_status in (
                DeliveryStatus.DELIVERED,
                DeliveryStatus.FAILED,
            ):
                raise InvalidInput("stored delivery semantics are invalid")

            if updated < created or delivery_updated < created:
                raise InvalidInput("stored timestamp order is invalid")
            if started is not None and (started < created or updated < started):
                raise InvalidInput("stored timestamp order is invalid")
            if ended is not None and (
                ended < created or (started is not None and ended < started)
            ):
                raise InvalidInput("stored timestamp order is invalid")
            if status is TaskStatus.QUEUED and (
                started is not None or ended is not None or deadline is not None
            ):
                raise InvalidInput("stored queued task is inconsistent")
            if status is TaskStatus.RUNNING and (started is None or ended is not None):
                raise InvalidInput("stored running task is inconsistent")
            if status in TERMINAL_TASK_STATUSES and (
                ended is None or updated != ended
            ):
                raise InvalidInput("stored terminal task is inconsistent")
            if timeout is None and deadline is not None:
                raise InvalidInput("stored deadline is inconsistent")
            if timeout is not None:
                expected_deadline = started + timeout if started is not None else None
                if deadline != expected_deadline or (
                    deadline is not None and not math.isfinite(deadline)
                ):
                    raise InvalidInput("stored deadline is inconsistent")
            if delivery_status is DeliveryStatus.PENDING:
                if delivery_updated != created:
                    raise InvalidInput("stored pending delivery is inconsistent")
            elif (
                status in ACTIVE_TASK_STATUSES
                or ended is None
                or delivery_updated < ended
            ):
                raise InvalidInput("stored delivery timing is inconsistent")
            minimum_version = (
                int(started is not None)
                + int(status in TERMINAL_TASK_STATUSES)
                + int(delivery_status in TERMINAL_DELIVERY_STATUSES)
            )
            if version < minimum_version:
                raise InvalidInput("stored version is inconsistent")
        except (InvalidInput, KeyError, TypeError, ValueError, OverflowError):
            raise StateConflict("stored task metadata failed validation") from None

        return Task(
            id=identifier,
            parent_id=parent,
            status=status,
            delivery_status=delivery_status,
            delivery_required=delivery_required,
            payload_hash=payload_hash,
            timeout_seconds=timeout,
            created_at=created,
            started_at=started,
            updated_at=updated,
            ended_at=ended,
            deadline_at=deadline,
            delivery_updated_at=delivery_updated,
            terminal_code=terminal_code,
            delivery_code=delivery_code,
            version=version,
        )

    @staticmethod
    def _get_row(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound("task does not exist")
        return row

    @staticmethod
    def _require_forward_time(now: float, *values: Optional[float]) -> None:
        for value in values:
            if value is not None and now < float(value):
                raise StateConflict("clock moved backwards across a persisted transition")

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        task_id: str,
        kind: str,
        from_value: Optional[str],
        to_value: str,
        occurred_at: float,
        code: Optional[str],
    ) -> None:
        connection.execute(
            """
            INSERT INTO task_events(
                task_id, event_kind, from_value, to_value, occurred_at, code
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, kind, from_value, to_value, occurred_at, code),
        )

    @staticmethod
    def _state_metadata_is_consistent(row: sqlite3.Row) -> bool:
        try:
            if validate_uuid(row["id"]) != row["id"]:
                return False
            if row["parent_id"] is not None:
                if validate_uuid(row["parent_id"]) != row["parent_id"]:
                    return False
            status = coerce_task_status(row["status"])
            delivery_status = coerce_delivery_status(row["delivery_status"])
            if row["delivery_required"] not in (0, 1):
                return False
            delivery_required = bool(row["delivery_required"])
            validate_payload_hash(row["payload_hash"])
            if row["timeout_seconds"] is not None:
                validate_nonnegative_number(row["timeout_seconds"], "timeout_seconds")
            if isinstance(row["version"], bool):
                return False
            version = int(row["version"])
            if version < 0 or version != row["version"]:
                return False
            if status in TERMINAL_TASK_STATUSES:
                validate_task_code(row["terminal_code"], status)
            elif row["terminal_code"] is not None:
                return False
            if delivery_status in TERMINAL_DELIVERY_STATUSES:
                validate_delivery_code(row["delivery_code"], delivery_status)
            elif row["delivery_code"] is not None:
                return False
            if delivery_required and delivery_status is DeliveryStatus.NOT_APPLICABLE:
                return False
            if not delivery_required and delivery_status in (
                DeliveryStatus.DELIVERED,
                DeliveryStatus.FAILED,
            ):
                return False
            if delivery_status in TERMINAL_DELIVERY_STATUSES and status in ACTIVE_TASK_STATUSES:
                return False
            minimum_version = (
                int(row["started_at"] is not None)
                + int(status in TERMINAL_TASK_STATUSES)
                + int(delivery_status in TERMINAL_DELIVERY_STATUSES)
            )
            if version < minimum_version:
                return False
        except (InvalidInput, TypeError, ValueError, OverflowError):
            return False
        return True

    @staticmethod
    def _event_chain_is_consistent(
        task_row: sqlite3.Row, event_rows: List[sqlite3.Row]
    ) -> bool:
        try:
            status = coerce_task_status(task_row["status"])
            delivery_status = coerce_delivery_status(task_row["delivery_status"])
            created = float(task_row["created_at"])
            started = (
                float(task_row["started_at"])
                if task_row["started_at"] is not None
                else None
            )
            ended = (
                float(task_row["ended_at"])
                if task_row["ended_at"] is not None
                else None
            )
            delivery_updated = float(task_row["delivery_updated_at"])
            expected = [("created", None, "queued", created, "created")]
            if started is not None:
                expected.append(
                    ("task_transition", "queued", "running", started, "started")
                )
            if status in TERMINAL_TASK_STATUSES:
                if ended is None:
                    return False
                validate_task_code(task_row["terminal_code"], status)
                expected.append(
                    (
                        "task_transition",
                        "running" if started is not None else "queued",
                        status.value,
                        ended,
                        task_row["terminal_code"],
                    )
                )
            elif status is TaskStatus.RUNNING and started is None:
                return False
            elif status is TaskStatus.QUEUED and started is not None:
                return False
            if delivery_status in TERMINAL_DELIVERY_STATUSES:
                if status in ACTIVE_TASK_STATUSES:
                    return False
                validate_delivery_code(task_row["delivery_code"], delivery_status)
                expected.append(
                    (
                        "delivery_transition",
                        "pending",
                        delivery_status.value,
                        delivery_updated,
                        task_row["delivery_code"],
                    )
                )
            if len(event_rows) != len(expected):
                return False
            for event, wanted in zip(event_rows, expected):
                occurred = float(event["occurred_at"])
                if not math.isfinite(occurred):
                    return False
                actual = (
                    event["event_kind"],
                    event["from_value"],
                    event["to_value"],
                    occurred,
                    event["code"],
                )
                if actual != wanted:
                    return False
        except (InvalidInput, TypeError, ValueError, OverflowError):
            return False
        return True

    def create_task(
        self,
        *,
        parent_id: Optional[str] = None,
        payload_hash: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        delivery_required: bool = True,
        task_id: Optional[str] = None,
    ) -> Task:
        parent = validate_uuid(parent_id) if parent_id is not None else None
        digest = validate_payload_hash(payload_hash)
        timeout = (
            validate_nonnegative_number(timeout_seconds, "timeout_seconds")
            if timeout_seconds is not None
            else None
        )
        if not isinstance(delivery_required, bool):
            raise InvalidInput("delivery_required must be bool")
        identifier = validate_uuid(task_id) if task_id is not None else str(uuid.uuid4())
        with self._transaction() as connection:
            now = self._now()
            existing = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (identifier,)
            ).fetchone()
            if existing is not None:
                task = self._task_from_row(existing)
                same = (
                    task.parent_id == parent
                    and task.payload_hash == digest
                    and task.timeout_seconds == timeout
                    and task.delivery_required == delivery_required
                )
                if not same:
                    raise StateConflict("task id already exists with different immutable fields")
                return task
            if parent is not None:
                parent_row = self._get_row(connection, parent)
                self._task_from_row(parent_row)
                self._require_forward_time(
                    now,
                    parent_row["created_at"],
                    parent_row["started_at"],
                    parent_row["updated_at"],
                    parent_row["ended_at"],
                    parent_row["delivery_updated_at"],
                )
            connection.execute(
                """
                INSERT INTO tasks(
                    id, parent_id, status, delivery_status, delivery_required,
                    payload_hash, timeout_seconds, created_at, started_at,
                    updated_at, ended_at, deadline_at, delivery_updated_at,
                    terminal_code, delivery_code, version
                ) VALUES (?, ?, 'queued', 'pending', ?, ?, ?, ?, NULL, ?, NULL,
                          NULL, ?, NULL, NULL, 0)
                """,
                (
                    identifier,
                    parent,
                    int(delivery_required),
                    digest,
                    timeout,
                    now,
                    now,
                    now,
                ),
            )
            self._event(connection, identifier, "created", None, "queued", now, "created")
            return self._task_from_row(self._get_row(connection, identifier))

    def get_task(self, task_id: str) -> Task:
        identifier = validate_uuid(task_id)
        connection = self._connect()
        try:
            return self._task_from_row(self._get_row(connection, identifier))
        finally:
            connection.close()

    def children_of(self, task_id: str) -> List[Task]:
        identifier = validate_uuid(task_id)
        connection = self._connect()
        try:
            self._task_from_row(self._get_row(connection, identifier))
            rows = connection.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at, id",
                (identifier,),
            ).fetchall()
            return [self._task_from_row(row) for row in rows]
        finally:
            connection.close()

    def start_task(self, task_id: str) -> Task:
        identifier = validate_uuid(task_id)
        with self._transaction() as connection:
            row = self._get_row(connection, identifier)
            current = self._task_from_row(row).status
            if current is TaskStatus.RUNNING:
                return self._task_from_row(row)
            if current is not TaskStatus.QUEUED:
                raise StateConflict("terminal tasks cannot be started")
            now = self._now()
            self._require_forward_time(now, row["created_at"], row["updated_at"])
            deadline = (
                now + float(row["timeout_seconds"])
                if row["timeout_seconds"] is not None
                else None
            )
            if deadline is not None and not math.isfinite(deadline):
                raise InvalidInput("timeout produces a non-finite deadline")
            connection.execute(
                """
                UPDATE tasks
                SET status = 'running', started_at = ?, updated_at = ?,
                    deadline_at = ?, version = version + 1
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, deadline, identifier),
            )
            self._event(
                connection,
                identifier,
                "task_transition",
                "queued",
                "running",
                now,
                "started",
            )
            return self._task_from_row(self._get_row(connection, identifier))

    def heartbeat(self, task_id: str) -> Task:
        identifier = validate_uuid(task_id)
        with self._transaction() as connection:
            row = self._get_row(connection, identifier)
            if self._task_from_row(row).status is not TaskStatus.RUNNING:
                raise StateConflict("only running tasks accept heartbeats")
            now = self._now()
            self._require_forward_time(
                now, row["created_at"], row["started_at"], row["updated_at"]
            )
            connection.execute(
                "UPDATE tasks SET updated_at = ?, version = version + 1 WHERE id = ?",
                (now, identifier),
            )
            return self._task_from_row(self._get_row(connection, identifier))

    def close_task(
        self,
        task_id: str,
        status: Union[str, TaskStatus],
        *,
        code: Optional[str] = None,
    ) -> Task:
        identifier = validate_uuid(task_id)
        target = coerce_task_status(status)
        if target not in TERMINAL_TASK_STATUSES:
            raise InvalidInput("close status must be terminal")
        with self._transaction() as connection:
            row = self._get_row(connection, identifier)
            current = self._task_from_row(row).status
            if current in TERMINAL_TASK_STATUSES:
                if current is target:
                    return self._task_from_row(row)
                raise StateConflict("task already has a different terminal state")
            reason = validate_task_code(code, target)
            now = self._now()
            self._require_forward_time(
                now, row["created_at"], row["started_at"], row["updated_at"]
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?, ended_at = ?, terminal_code = ?,
                    version = version + 1
                WHERE id = ? AND status IN ('queued','running')
                """,
                (target.value, now, now, reason, identifier),
            )
            self._event(
                connection,
                identifier,
                "task_transition",
                current.value,
                target.value,
                now,
                reason,
            )
            return self._task_from_row(self._get_row(connection, identifier))

    def set_delivery(
        self,
        task_id: str,
        status: Union[str, DeliveryStatus],
        *,
        code: Optional[str] = None,
    ) -> Task:
        identifier = validate_uuid(task_id)
        target = coerce_delivery_status(status)
        if target not in TERMINAL_DELIVERY_STATUSES:
            raise InvalidInput("delivery close status must be terminal")
        with self._transaction() as connection:
            row = self._get_row(connection, identifier)
            stored_task = self._task_from_row(row)
            task_status = stored_task.status
            current = stored_task.delivery_status
            if task_status in ACTIVE_TASK_STATUSES:
                raise StateConflict("delivery cannot close before the task is terminal")
            if current in TERMINAL_DELIVERY_STATUSES:
                if current is target:
                    return self._task_from_row(row)
                raise StateConflict("delivery already has a different terminal state")
            delivery_required = bool(row["delivery_required"])
            if delivery_required and target is DeliveryStatus.NOT_APPLICABLE:
                raise StateConflict("delivery-required tasks cannot be not_applicable")
            if not delivery_required and target is not DeliveryStatus.NOT_APPLICABLE:
                raise StateConflict("internal tasks require not_applicable delivery")
            reason = validate_delivery_code(code, target)
            now = self._now()
            self._require_forward_time(
                now,
                row["created_at"],
                row["updated_at"],
                row["ended_at"],
                row["delivery_updated_at"],
            )
            connection.execute(
                """
                UPDATE tasks
                SET delivery_status = ?, delivery_updated_at = ?, delivery_code = ?,
                    version = version + 1
                WHERE id = ? AND delivery_status = 'pending'
                """,
                (target.value, now, reason, identifier),
            )
            self._event(
                connection,
                identifier,
                "delivery_transition",
                current.value,
                target.value,
                now,
                reason,
            )
            return self._task_from_row(self._get_row(connection, identifier))

    def reconcile(
        self,
        *,
        active_grace_seconds: float,
        delivery_grace_seconds: float,
        dry_run: bool = False,
    ) -> Dict[str, Union[bool, int]]:
        if not isinstance(dry_run, bool):
            raise InvalidInput("dry_run must be bool")
        active_grace = validate_nonnegative_number(
            active_grace_seconds, "active_grace_seconds"
        )
        delivery_grace = validate_nonnegative_number(
            delivery_grace_seconds, "delivery_grace_seconds"
        )
        report = {
            "dry_run": dry_run,
            "applied": not dry_run,
            "active_examined": 0,
            "tasks_timed_out": 0,
            "fresh_active_retained": 0,
            "pending_delivery_examined": 0,
            "deliveries_failed": 0,
            "deliveries_not_applicable": 0,
            "pending_delivery_retained": 0,
        }
        with self._transaction(read_only=dry_run) as connection:
            active_query = (
                "SELECT * FROM tasks WHERE status IN ('queued','running') "
                "ORDER BY created_at, id"
            )
            if dry_run:
                active_rows = connection.execute(active_query).fetchall()
                # A deferred read transaction fixes its snapshot on the first
                # read. Sample the clock afterwards so a concurrent heartbeat
                # cannot appear later than the preview's decision time.
                now = self._now()
            else:
                # Preserve the established apply-mode decision time. Its
                # BEGIN IMMEDIATE already prevents a concurrent writer race.
                now = self._now()
                active_rows = connection.execute(active_query).fetchall()
            report["active_examined"] = len(active_rows)
            task_transitions: List[Tuple[sqlite3.Row, str]] = []
            for row in active_rows:
                self._task_from_row(row)
                status = TaskStatus(row["status"])
                deadline_expired = (
                    status is TaskStatus.RUNNING
                    and row["deadline_at"] is not None
                    and float(row["deadline_at"]) <= now
                )
                restart_stale = (
                    status is TaskStatus.RUNNING
                    and float(row["updated_at"]) <= now - active_grace
                )
                if not (deadline_expired or restart_stale):
                    report["fresh_active_retained"] += 1
                    continue
                self._require_forward_time(
                    now, row["created_at"], row["started_at"], row["updated_at"]
                )
                code = "deadline_exceeded" if deadline_expired else "restart_stale"
                task_transitions.append((row, code))
                report["tasks_timed_out"] += 1

            pending_rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE delivery_status = 'pending'
                  AND status IN ('succeeded','failed','timed_out','cancelled')
                ORDER BY ended_at, id
                """
            ).fetchall()
            pending_candidates = []
            for row in pending_rows:
                self._task_from_row(row)
                pending_candidates.append((row, float(row["ended_at"])))
            pending_candidates.extend(
                (row, now) for row, _code in task_transitions
            )
            report["pending_delivery_examined"] = len(pending_candidates)
            delivery_transitions: List[
                Tuple[sqlite3.Row, DeliveryStatus, str]
            ] = []
            for row, ended_at in pending_candidates:
                if ended_at > now - delivery_grace:
                    report["pending_delivery_retained"] += 1
                    continue
                if bool(row["delivery_required"]):
                    target = DeliveryStatus.FAILED
                    code = "delivery_grace_expired"
                    report["deliveries_failed"] += 1
                else:
                    target = DeliveryStatus.NOT_APPLICABLE
                    code = "internal_task"
                    report["deliveries_not_applicable"] += 1
                delivery_transitions.append((row, target, code))

            if dry_run:
                return report

            for row, code in task_transitions:
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'timed_out', updated_at = ?, ended_at = ?,
                        terminal_code = ?, version = version + 1
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, now, code, row["id"]),
                )
                self._event(
                    connection,
                    row["id"],
                    "task_transition",
                    "running",
                    "timed_out",
                    now,
                    code,
                )

            for row, target, code in delivery_transitions:
                connection.execute(
                    """
                    UPDATE tasks
                    SET delivery_status = ?, delivery_updated_at = ?, delivery_code = ?,
                        version = version + 1
                    WHERE id = ? AND delivery_status = 'pending'
                    """,
                    (target.value, now, code, row["id"]),
                )
                self._event(
                    connection,
                    row["id"],
                    "delivery_transition",
                    "pending",
                    target.value,
                    now,
                    code,
                )
        return report

    def doctor(self) -> Dict[str, Any]:
        """Return aggregate health data without task IDs or payload hashes."""

        connection = self._connect()
        try:
            try:
                quick_rows = connection.execute("PRAGMA quick_check").fetchall()
                quick_ok = bool(quick_rows) and all(row[0] == "ok" for row in quick_rows)
            except sqlite3.Error:
                quick_ok = False

            try:
                actual_version, metadata_ok = self._schema_metadata_state(connection)
                schema_ok = (
                    actual_version == self.SCHEMA_VERSION
                    and metadata_ok
                    and self._schema_objects(connection) == self._SCHEMA_OBJECTS
                    and self._schema_signature(connection) == self._SCHEMA_FINGERPRINT
                )
            except (sqlite3.Error, TypeError, ValueError):
                actual_version = None
                schema_ok = False

            empty_statuses = {status.value: 0 for status in TaskStatus}
            empty_deliveries = {status.value: 0 for status in DeliveryStatus}
            if not schema_ok:
                return {
                    "healthy": False,
                    "quick_check": "ok" if quick_ok else "failed",
                    "schema_version": actual_version,
                    "schema_ok": False,
                    "task_count": 0,
                    "event_count": 0,
                    "task_status_counts": empty_statuses,
                    "delivery_status_counts": empty_deliveries,
                    "orphan_count": 0,
                    "parent_cycle_count": 0,
                    "foreign_key_error_count": 0,
                    "state_inconsistency_count": 0,
                    "timestamp_inconsistency_count": 0,
                    "event_inconsistency_count": 0,
                    "doctor_error_count": 1,
                }

            task_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
                ).fetchall()
            }
            delivery_counts = {
                row["delivery_status"]: row["count"]
                for row in connection.execute(
                    "SELECT delivery_status, COUNT(*) AS count FROM tasks GROUP BY delivery_status"
                ).fetchall()
            }
            orphan_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM tasks AS child
                LEFT JOIN tasks AS parent ON parent.id = child.parent_id
                WHERE child.parent_id IS NOT NULL AND parent.id IS NULL
                """
            ).fetchone()[0]
            task_rows = connection.execute("SELECT * FROM tasks").fetchall()
            parent_by_id = {row["id"]: row["parent_id"] for row in task_rows}
            parent_cycle_count = 0
            completed_nodes = set()
            for start in parent_by_id:
                path = []
                positions = {}
                current = start
                while current in parent_by_id and current not in completed_nodes:
                    if current in positions:
                        parent_cycle_count += 1
                        break
                    positions[current] = len(path)
                    path.append(current)
                    parent = parent_by_id[current]
                    if parent is None:
                        break
                    current = parent
                completed_nodes.update(path)
            terminal_values = {status.value for status in TERMINAL_TASK_STATUSES}
            created_by_id = {}
            for row in task_rows:
                try:
                    created = float(row["created_at"])
                    if math.isfinite(created):
                        created_by_id[row["id"]] = created
                except (TypeError, ValueError, OverflowError):
                    pass
            parent_time_bad = set()
            for row in task_rows:
                parent_id = row["parent_id"]
                if parent_id is None or parent_id not in created_by_id:
                    continue
                child_created = created_by_id.get(row["id"])
                if (
                    child_created is not None
                    and child_created < created_by_id[parent_id]
                ):
                    parent_time_bad.add(row["id"])

            state_inconsistencies = sum(
                int(not self._state_metadata_is_consistent(row)) for row in task_rows
            )
            timestamp_inconsistencies = 0
            for row in task_rows:
                try:
                    created = float(row["created_at"])
                    updated = float(row["updated_at"])
                    delivery_updated = float(row["delivery_updated_at"])
                    started = (
                        float(row["started_at"])
                        if row["started_at"] is not None
                        else None
                    )
                    ended = (
                        float(row["ended_at"])
                        if row["ended_at"] is not None
                        else None
                    )
                    deadline = (
                        float(row["deadline_at"])
                        if row["deadline_at"] is not None
                        else None
                    )
                    timeout = (
                        float(row["timeout_seconds"])
                        if row["timeout_seconds"] is not None
                        else None
                    )
                    numeric_values = (
                        created,
                        updated,
                        delivery_updated,
                        started,
                        ended,
                        deadline,
                        timeout,
                    )
                    expected_deadline = (
                        started + timeout
                        if started is not None and timeout is not None
                        else None
                    )
                    inconsistent = (
                        not all(
                            value is None or math.isfinite(value)
                            for value in numeric_values
                        )
                        or updated < created
                        or delivery_updated < created
                        or (started is not None and started < created)
                        or (started is not None and updated < started)
                        or (ended is not None and ended < created)
                        or (
                            started is not None
                            and ended is not None
                            and ended < started
                        )
                        or (
                            row["status"] in terminal_values
                            and ended is not None
                            and updated != ended
                        )
                        or (
                            deadline is not None
                            and (started is None or deadline < started)
                        )
                        or (timeout is None and deadline is not None)
                        or (
                            timeout is not None
                            and (
                                timeout < 0
                                or deadline != expected_deadline
                                or (
                                    expected_deadline is not None
                                    and not math.isfinite(expected_deadline)
                                )
                            )
                        )
                        or (
                            row["status"] == TaskStatus.QUEUED.value
                            and (started is not None or deadline is not None)
                        )
                        or (
                            row["status"] == TaskStatus.RUNNING.value
                            and started is None
                        )
                        or (row["status"] in terminal_values and ended is None)
                        or (
                            row["delivery_status"] == DeliveryStatus.PENDING.value
                            and delivery_updated != created
                        )
                        or (
                            row["delivery_status"] != DeliveryStatus.PENDING.value
                            and (
                                ended is None
                                or delivery_updated < ended
                            )
                        )
                        or row["id"] in parent_time_bad
                    )
                except (TypeError, ValueError, OverflowError):
                    inconsistent = True
                timestamp_inconsistencies += int(inconsistent)

            all_events = connection.execute(
                "SELECT * FROM task_events ORDER BY task_id, sequence"
            ).fetchall()
            event_rows_by_task = {}
            for event in all_events:
                event_rows_by_task.setdefault(event["task_id"], []).append(event)
            event_count = len(all_events)
            event_inconsistencies = sum(
                int(
                    not self._event_chain_is_consistent(
                        row, event_rows_by_task.get(row["id"], [])
                    )
                )
                for row in task_rows
            )
            foreign_key_errors = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            total_tasks = sum(task_counts.values())
            report = {
                "healthy": (
                    quick_ok
                    and schema_ok
                    and orphan_count == 0
                    and parent_cycle_count == 0
                    and foreign_key_errors == 0
                    and state_inconsistencies == 0
                    and timestamp_inconsistencies == 0
                    and event_inconsistencies == 0
                ),
                "quick_check": "ok" if quick_ok else "failed",
                "schema_version": actual_version,
                "schema_ok": schema_ok,
                "task_count": total_tasks,
                "event_count": event_count,
                "task_status_counts": {
                    status.value: task_counts.get(status.value, 0) for status in TaskStatus
                },
                "delivery_status_counts": {
                    status.value: delivery_counts.get(status.value, 0)
                    for status in DeliveryStatus
                },
                "orphan_count": orphan_count,
                "parent_cycle_count": parent_cycle_count,
                "foreign_key_error_count": foreign_key_errors,
                "state_inconsistency_count": state_inconsistencies,
                "timestamp_inconsistency_count": timestamp_inconsistencies,
                "event_inconsistency_count": event_inconsistencies,
                "doctor_error_count": 0,
            }
            return report
        finally:
            connection.close()

    def storage_info(self) -> Dict[str, Any]:
        """Return non-sensitive SQLite configuration for diagnostics and tests."""

        connection = self._connect()
        try:
            return {
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                "busy_timeout_ms": connection.execute("PRAGMA busy_timeout").fetchone()[0],
                "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "permission_model": self._permission_model,
            }
        finally:
            connection.close()
