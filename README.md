# TaskStateGuard（任务状态守护）

TaskStateGuard is a small, standard-library-only task-state engine for agent runtimes that
must survive process restarts without inventing success. It records lifecycle
metadata, parent/child relationships, delivery state, and opaque SHA-256 payload
fingerprints in SQLite. It never needs the prompt or task body.

中文简介：为智能体任务提供崩溃安全的状态记录与重启收尾，区分“任务是否结束”和“结果是否交付”，且不保存提示词或任务正文。

This repository is a clean-room prototype. It was designed from a public problem
statement and does not contain production databases, private fixtures, copied
runtime code, or Git history from another project.

## Why it exists

Agent runtimes often have two different truths:

1. whether work finished; and
2. whether its result was delivered.

Conflating them produces misleading states such as a timed-out task that remains
"pending" forever, or a successful internal child task that appears to require a
user-facing delivery. TaskStateGuard models both state machines explicitly and
reconciles stale work after a restart with configurable grace periods.

## Properties

- Python 3.11+ and no third-party runtime dependencies.
- No network code and no telemetry.
- Atomic SQLite writes with WAL, foreign keys, `busy_timeout`, and `quick_check`.
- Exact schema fingerprinting, aggregate state/event diagnostics, and a guarded
  schema-v1 to schema-v2 metadata migration.
- On POSIX hosts, a `0600` database and sidecars plus `0700` newly created
  directories; on every supported host, refusal of observable database/sidecar
  symlink, Windows reparse-point, or hardlink aliases.
- UUID task identifiers and optional opaque SHA-256 payload fingerprints.
- No column or CLI flag for prompt text, message bodies, or user content.
- Idempotent task closing and delivery closing.
- Explicit parent/child relationships and internal, non-deliverable tasks.
- Injected clock for deterministic recovery tests.
- A counts-only doctor that never emits task IDs or payload fingerprints.
- Value-free JSON CLI errors: rejected arguments, paths, and exceptions are not
  echoed.

## State model

Task states:

```text
queued -> running -> succeeded | failed | timed_out | cancelled
   \----------------^----------^----------^----------^
```

Terminal states are immutable. Repeating the same close is safe; attempting to
replace one terminal state with another raises a conflict.

Delivery states:

```text
pending -> delivered | failed | not_applicable
```

Delivery is independent from task execution. `not_applicable` is intended for
internal work whose parent or surrounding flow owns the user-facing delivery.
Delivery-required tasks cannot become `not_applicable`; internal tasks cannot be
marked `delivered` or delivery `failed`.

## Quick start

Run directly from a checkout:

```bash
export PYTHONPATH=src
python -m task_state_guard --db ./task-state-guard.sqlite init
python -m task_state_guard --db ./task-state-guard.sqlite create --timeout-seconds 900
python -m task_state_guard --db ./task-state-guard.sqlite doctor
```

The create command accepts `--payload-sha256`, never a plaintext payload. CLI
and default `Task.to_dict()` output expose only `has_payload_hash`, not the
correlatable hash. Trusted in-process callers may explicitly request it with
`task.to_dict(include_payload_hash=True)`. In
Python, callers can fingerprint content before crossing the storage boundary:

```python
from task_state_guard import Ledger, payload_fingerprint

ledger = Ledger("task-state-guard.sqlite")
task = ledger.create_task(payload_hash=payload_fingerprint(b"ephemeral input"))
ledger.start_task(task.id)
ledger.close_task(task.id, "succeeded", code="worker_completed")
ledger.set_delivery(task.id, "delivered", code="transport_acknowledged")
```

On Windows, Python's standard library cannot verify or install a private DACL
equivalent to POSIX mode `0600`. TaskStateGuard therefore fails closed by
default. First create the database directory with an ACL restricted to the
service account, then explicitly acknowledge that external boundary:

```python
ledger = Ledger(r"C:\service-state\task-state-guard.sqlite", allow_external_acl=True)
```

The CLI equivalent places `--allow-external-acl` before the subcommand. This
flag does not create, inspect, or certify a Windows ACL; it only records the
caller's acknowledgement that the pre-existing directory is externally
protected.

Reason codes use a fixed registry and must match the requested terminal state.
For example, `worker_completed` belongs to task `succeeded`, `worker_failed` to
task `failed`, and `transport_acknowledged` to delivery `delivered`. Free-form
exception strings, model output, paths, and user text do not belong in this
database.

## CLI output and privacy contract

For ordinary non-help invocations, the CLI writes one compact, key-sorted JSON
line to standard output and nothing to standard error. Expected validation or
state errors disclose only their exception class; unexpected exceptions become
the fixed `InternalError` category. Rejected argument values, filesystem paths,
and exception messages are not echoed. Exit status is deterministic:

- `0`: the command succeeded, including a healthy `doctor` report;
- `1`: `doctor` completed and found an inconsistent or unhealthy ledger;
- `2`: a bounded input, storage, or state error occurred; and
- `3`: an unexpected internal error occurred.

Successful task commands intentionally return operational metadata including
UUIDs, parent UUIDs, states, and timestamps. Protect or redirect that output as
you would the database. The optional SHA-256 fingerprint is stored in SQLite by
design, but is hidden from default CLI and object serialization; it remains
correlatable pseudonymous metadata rather than anonymized content. Raw prompts,
task bodies, credentials, paths, and exception strings are unsupported inputs
and have no storage column.

Public terminal mappings are:

- task `succeeded`: `completed`, `worker_completed`;
- task `failed`: `worker_failed`;
- task `timed_out`: `deadline_exceeded`, `restart_stale`;
- task `cancelled`: `caller_cancelled`;
- delivery `delivered`: `transport_acknowledged`;
- delivery `failed`: `delivery_grace_expired`, `transport_failed`; and
- delivery `not_applicable`: `internal_task`.

Codes are optional. `created` and `started` are reserved for ledger-generated
events. An idempotent retry of an already-recorded identical terminal state does
not replace its original reason code.

## Storage boundary

Use a dedicated local-filesystem directory controlled by the service account.
On Linux and macOS, its final directory may not be group- or world-writable,
writable ancestors must have safe sticky-directory semantics, newly created path
components use mode `0700`, and the database and existing SQLite sidecars are
tightened to mode `0600`. On Windows, the complete parent path must already
exist under a private externally managed DACL and callers must opt in with
`allow_external_acl=True` or `--allow-external-acl`; TaskStateGuard cannot
validate that DACL with the Python standard library. `storage_info()` reports
the active `permission_model` as `posix_mode` or `external_acl`.

On all supported hosts, existing SQLite `-journal`, `-wal`, and `-shm` sidecars
are rejected if they are observable symlinks, Windows reparse points, or have
multiple hardlinks. A direct database alias of those kinds is likewise rejected.
These checks require stable file identity and link-count reporting from the
filesystem.

TaskStateGuard does not create application-managed scratch or export files in
the state directory. Normal operation creates only the database and SQLite's
own `-journal`, `-wal`, or `-shm` sidecars. It opens no network connection and
contains no telemetry integration.

TaskStateGuard canonicalizes the parent path before binding the database identity.
It supports local filesystems with SQLite-compatible locking and stable file
identity. UNC paths, mapped network drives, remote mounts, and filesystems with
unreliable inode or hardlink reporting are unsupported. TaskStateGuard cannot
reliably identify every mapped or mounted remote filesystem, so deployment on a
local disk is an operator requirement. It assumes processes with the same OS
user identity are trusted; see `THREAT_MODEL.md` for the remaining limits.

## Restart reconciliation

```bash
python -m task_state_guard --db ./task-state-guard.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600
```

The reconciler performs one atomic transaction:

- a running task past its explicit deadline becomes `timed_out`;
- a running task with no recent heartbeat beyond the active grace becomes
  `timed_out`;
- queued tasks and fresh running tasks remain active;
- a terminal, delivery-required task still pending beyond delivery grace becomes
  delivery `failed`;
- a terminal internal task beyond delivery grace becomes `not_applicable`.

The report contains counts only, never task IDs.

## Doctor and migration

`doctor` checks SQLite integrity, the exact schema fingerprint and version,
the exact closed set and values of schema metadata keys,
foreign keys, parent ordering and cycles, canonical identifiers, task/delivery
semantics, finite timestamp order, and the complete expected event chain for
every task. Historical findings make
`healthy` false but are never rendered with task identifiers or stored values.
The CLI returns status `1` whenever the completed report has `healthy=false`, so
automation cannot mistake a rendered but unhealthy report for success.

Schema v1 is migrated transactionally to v2 only when its schema is exact and
all stored reason metadata already belongs to the fixed registry and matching
state. A v1 database containing arbitrary legacy reason text fails closed; make
a SQLite-safe backup and perform an explicit private migration instead of
logging or copying rejected values.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3.12 scripts/privacy_audit.py
PYTHONDONTWRITEBYTECODE=1 python3.12 scripts/privacy_audit.py --self-test
PYTHONDONTWRITEBYTECODE=1 python3.12 scripts/privacy_audit.py --sdist /path/to/extracted-sdist
```

The synthetic suite covers restart grace, deadlines, cancellation, parent/child
relationships and clocks, delivery reconciliation, concurrent idempotent closes,
schema/event tampering, path and sidecar aliases, private permissions, CLI
redaction, deterministic doctor behavior, and absence of rejected or plaintext
task values from output and SQLite database/WAL/SHM files. The publication audit
checks the release manifest and metadata, common secret and personal-data
patterns, generated or persistent artifacts, unsafe file modes, SPDX headers,
Python syntax, and network/telemetry imports. Its report contains only relative
path, category, and count, never matched source text. `--self-test` plants
synthetic canaries in a temporary directory, confirms those checks fail closed,
and verifies that the canary values are absent from its report. Source-tree mode
rejects every generated `*.egg-info` directory. `--sdist` requires, allows, and
continues scanning only the standard `src/task_state_guard.egg-info` directory
inside an extracted source distribution; an `egg-info` directory at any other
path, or any additional generated directory, remains a hard finding.

CI runs the suite on Ubuntu with Python 3.11 through 3.14 and on macOS and
Windows with Python 3.11 and 3.14. The Windows jobs validate the explicit
external-ACL boundary and portable state/locking behavior; they do not claim to
audit or establish a Windows DACL.

## Project status

This pre-release clean-room implementation is licensed under the Apache License,
Version 2.0; see `LICENSE`. See `PROVENANCE.md` for the clean-room and human
review record, `CONTRIBUTING.md` for contribution guidance, `SECURITY.md` for
reporting guidance, and `THREAT_MODEL.md` for the exact privacy boundary and
non-goals.
