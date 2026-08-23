# SPDX-License-Identifier: Apache-2.0
"""Restart reconciliation policy and counts-only wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Union

from .model import validate_nonnegative_number
from .store import Ledger


@dataclass(frozen=True)
class ReconcilePolicy:
    active_grace_seconds: float = 600.0
    delivery_grace_seconds: float = 600.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "active_grace_seconds",
            validate_nonnegative_number(self.active_grace_seconds, "active_grace_seconds"),
        )
        object.__setattr__(
            self,
            "delivery_grace_seconds",
            validate_nonnegative_number(
                self.delivery_grace_seconds, "delivery_grace_seconds"
            ),
        )


def reconcile_restart(
    ledger: Ledger,
    policy: ReconcilePolicy,
    *,
    dry_run: bool = False,
) -> Dict[str, Union[bool, int]]:
    """Preview or apply restart reconciliation and return aggregate counts."""

    return ledger.reconcile(
        active_grace_seconds=policy.active_grace_seconds,
        delivery_grace_seconds=policy.delivery_grace_seconds,
        dry_run=dry_run,
    )
