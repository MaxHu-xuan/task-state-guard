# SPDX-License-Identifier: Apache-2.0
"""Public value types and validation helpers."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union


class LedgerError(Exception):
    """Base class for expected ledger errors."""


class InvalidInput(LedgerError, ValueError):
    """A caller supplied a value outside the public contract."""


class TaskNotFound(LedgerError, LookupError):
    """The requested task does not exist."""


class StateConflict(LedgerError):
    """A transition conflicts with an already-recorded state."""


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


ACTIVE_TASK_STATUSES = frozenset((TaskStatus.QUEUED, TaskStatus.RUNNING))
TERMINAL_TASK_STATUSES = frozenset(
    (
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
    )
)
TERMINAL_DELIVERY_STATUSES = frozenset(
    (DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.NOT_APPLICABLE)
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ALLOWED_REASON_CODES = frozenset(
    (
        "caller_cancelled",
        "completed",
        "created",
        "deadline_exceeded",
        "delivery_grace_expired",
        "internal_task",
        "restart_stale",
        "started",
        "transport_acknowledged",
        "transport_failed",
        "worker_completed",
        "worker_failed",
    )
)
TASK_REASON_CODES = {
    TaskStatus.SUCCEEDED: frozenset(("completed", "worker_completed")),
    TaskStatus.FAILED: frozenset(("worker_failed",)),
    TaskStatus.TIMED_OUT: frozenset(("deadline_exceeded", "restart_stale")),
    TaskStatus.CANCELLED: frozenset(("caller_cancelled",)),
}
DELIVERY_REASON_CODES = {
    DeliveryStatus.DELIVERED: frozenset(("transport_acknowledged",)),
    DeliveryStatus.FAILED: frozenset(("delivery_grace_expired", "transport_failed")),
    DeliveryStatus.NOT_APPLICABLE: frozenset(("internal_task",)),
}


def payload_fingerprint(payload: Union[str, bytes]) -> str:
    """Return a SHA-256 fingerprint without retaining the supplied payload."""

    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raise InvalidInput("payload must be str or bytes")
    return hashlib.sha256(raw).hexdigest()


def validate_payload_hash(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise InvalidInput("payload_hash must be a lowercase SHA-256 hex digest")
    return value


def validate_code(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise InvalidInput("code must be 1-64 lowercase machine-readable characters")
    if value not in ALLOWED_REASON_CODES:
        raise InvalidInput("code is not in the public reason-code registry")
    return value


def validate_task_code(value: Optional[str], status: TaskStatus) -> Optional[str]:
    code = validate_code(value)
    if code is not None and code not in TASK_REASON_CODES.get(status, frozenset()):
        raise InvalidInput("code is not valid for the requested task status")
    return code


def validate_delivery_code(
    value: Optional[str], status: DeliveryStatus
) -> Optional[str]:
    code = validate_code(value)
    if code is not None and code not in DELIVERY_REASON_CODES.get(status, frozenset()):
        raise InvalidInput("code is not valid for the requested delivery status")
    return code


def validate_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidInput("task id must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise InvalidInput("task id must be a valid UUID") from None
    return str(parsed)


def validate_nonnegative_number(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise InvalidInput("%s must be a finite nonnegative number" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidInput("%s must be a finite nonnegative number" % field) from None
    if not math.isfinite(number) or number < 0:
        raise InvalidInput("%s must be a finite nonnegative number" % field)
    return number


def coerce_task_status(value: Union[str, TaskStatus]) -> TaskStatus:
    try:
        return value if isinstance(value, TaskStatus) else TaskStatus(value)
    except ValueError:
        raise InvalidInput("unknown task status") from None


def coerce_delivery_status(value: Union[str, DeliveryStatus]) -> DeliveryStatus:
    try:
        return value if isinstance(value, DeliveryStatus) else DeliveryStatus(value)
    except ValueError:
        raise InvalidInput("unknown delivery status") from None


@dataclass(frozen=True)
class Task:
    id: str
    parent_id: Optional[str]
    status: TaskStatus
    delivery_status: DeliveryStatus
    delivery_required: bool
    payload_hash: Optional[str]
    timeout_seconds: Optional[float]
    created_at: float
    started_at: Optional[float]
    updated_at: float
    ended_at: Optional[float]
    deadline_at: Optional[float]
    delivery_updated_at: float
    terminal_code: Optional[str]
    delivery_code: Optional[str]
    version: int

    def to_dict(self, *, include_payload_hash: bool = False) -> Dict[str, Any]:
        """Return metadata, hiding the correlatable payload hash by default."""

        result = {
            "id": self.id,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "delivery_status": self.delivery_status.value,
            "delivery_required": self.delivery_required,
            "has_payload_hash": self.payload_hash is not None,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "deadline_at": self.deadline_at,
            "delivery_updated_at": self.delivery_updated_at,
            "terminal_code": self.terminal_code,
            "delivery_code": self.delivery_code,
            "version": self.version,
        }
        if include_payload_hash:
            result["payload_hash"] = self.payload_hash
        return result
