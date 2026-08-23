# TaskStateGuard（任务状态守护）

Crash-safe task and delivery state without storing task bodies.

一个不保存任务正文、能在进程重启后如实收敛任务与投递状态的 SQLite 状态机。

[![CI](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml)

TaskStateGuard is a small, standard-library-only task-state engine for agent runtimes that
must survive process restarts without inventing success. It records lifecycle
metadata, parent/child relationships, delivery state, and opaque SHA-256 payload
fingerprints in SQLite. It never needs the prompt or task body.

中文简介：为智能体任务提供崩溃安全的状态记录与重启收尾，区分“任务是否结束”和“结果是否交付”，且不保存提示词或任务正文。

This repository is a clean-room prototype. It was designed from a public problem
statement and does not contain production databases, private fixtures, copied
runtime code, or Git history from another project.

It is a state ledger, not a scheduler, queue, worker, retry engine, transport, or
proof that an external side effect occurred. The surrounding runtime remains
responsible for executing work and recording real transport acknowledgements.

中文定位：它只记录和校验状态，不负责执行、重试、调度或发送任务，也不会把超时、
失联或未确认的外部副作用推测为成功。

## What TaskStateGuard solves / 它解决什么

TaskStateGuard answers one narrow operational question: after an AI agent or
worker process restarts, which tasks are still active, which have reached a real
terminal state, and which results still lack a transport acknowledgement? It
persists bounded state metadata in SQLite and reconciles stale records without
guessing that work or delivery succeeded.

一句话说明：它是面向 AI 智能体与后台 worker 的“重启安全任务状态账本”，用于收敛
SQLite 中的卡死任务和待投递状态；它不会恢复任务正文，也不会伪造成功或已送达。

| Common question / 常见需求 | TaskStateGuard's answer / 能力边界 |
|---|---|
| How do I reconcile stale agent tasks after a process restart? / 进程重启后如何收敛卡死任务？ | Call `reconcile_restart()` or the `reconcile` CLI with explicit active and delivery grace periods. |
| How do I track task completion separately from result delivery? / 如何区分任务完成和结果送达？ | Independent task and delivery state machines preserve both facts. |
| How do I audit a SQLite task ledger without exposing task data? / 如何安全诊断 SQLite 任务账本？ | `doctor` returns aggregate counts and health signals, never task IDs or payload fingerprints. |
| Can the same state ledger run on Linux, macOS, and Windows? / 是否跨平台？ | Yes, with POSIX mode enforcement on Linux/macOS and an explicit externally managed ACL boundary on Windows. |
| Does restart recovery require storing prompts or task bodies? / 是否需要保存提示词或正文？ | No. The schema has no plaintext payload field; an optional validated SHA-256 fingerprint is the only payload-derived value. |

Use TaskStateGuard when a trusted Python runtime already executes work but needs
a small, local, restart-safe source of truth for task lifecycle and delivery
acknowledgement. Do not use it as a distributed queue, workflow orchestrator,
process supervisor, retry service, transport, or exactly-once guarantee.

## Why it exists

Agent runtimes often have two different truths:

1. whether work finished; and
2. whether its result was delivered.

Conflating them produces misleading states such as a timed-out task that remains
"pending" forever, or a successful internal child task that appears to require a
user-facing delivery. TaskStateGuard models both state machines explicitly and
reconciles stale work after a restart with configurable grace periods.

## Properties

- CPython 3.11 through 3.14 and no third-party runtime dependencies.
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

## Install from a reviewed checkout

TaskStateGuard supports CPython 3.11 through 3.14 and has no third-party runtime
dependencies. From a cloned and reviewed source tree:

Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
task-state-guard --help
task-state-guard --version
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\task-state-guard.exe --help
.\.venv\Scripts\task-state-guard.exe --version
```

Installing from a checkout invokes the build backend and may download build-time
requirements. Runtime operation itself is standard-library-only and performs no
network access. Review the checkout and use your normal dependency controls
before building in a sensitive environment.

## Quick start from source

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

This synthetic end-to-end check is portable across the supported operating
systems. On Windows, the temporary directory is pre-existing and the example
explicitly acknowledges its externally managed ACL; this is a functional test,
not evidence that the temporary directory has a production-quality DACL.

```python
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from task_state_guard import Ledger, TaskStatus

with TemporaryDirectory() as directory:
    ledger = Ledger(
        Path(directory) / "ledger.sqlite",
        allow_external_acl=os.name == "nt",
    )
    task = ledger.create_task(timeout_seconds=30)
    ledger.start_task(task.id)
    finished = ledger.close_task(task.id, "succeeded", code="completed")
    ledger.set_delivery(
        task.id,
        "delivered",
        code="transport_acknowledged",
    )
    assert finished.status is TaskStatus.SUCCEEDED
    assert ledger.doctor()["healthy"] is True
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

## Platform contract

| Host | Directory and file boundary | Required caller action |
|---|---|---|
| Linux | Local filesystem; private owner-controlled leaf; safe writable ancestors; database and sidecars tightened to `0600` | Use a dedicated service-account directory; do not use remote mounts or aliases |
| macOS | Same POSIX checks as Linux; local SQLite-compatible filesystem required | Use a dedicated service-account directory; do not use aliases or shared writable paths |
| Windows | Complete parent path must already exist; observable reparse points, symlinks, and hardlinks are rejected; DACL privacy cannot be verified by the standard library | Restrict the directory DACL externally, then pass `allow_external_acl=True` or `--allow-external-acl` |

Windows acknowledgement does not emulate `chmod(0o600)`, inspect a DACL, or make
a shared directory private. Linux and macOS mode enforcement does not make
network filesystems supported.

Reason codes use a fixed registry and must match the requested terminal state.
For example, `worker_completed` belongs to task `succeeded`, `worker_failed` to
task `failed`, and `transport_acknowledged` to delivery `delivered`. Free-form
exception strings, model output, paths, and user text do not belong in this
database.

## CLI output and privacy contract

For ordinary state/database invocations other than `--help` and `--version`, the
CLI writes one compact, key-sorted JSON line to standard output and nothing to
standard error. Expected validation or state errors disclose only their
exception class; unexpected exceptions become the fixed `InternalError`
category. Rejected argument values, filesystem paths, and exception messages
are not echoed. Exit status is deterministic:

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

The same operation is available to an embedded Python runtime:

```python
from task_state_guard import Ledger, ReconcilePolicy, reconcile_restart

ledger = Ledger("./task-state-guard.sqlite")
counts = reconcile_restart(
    ledger,
    ReconcilePolicy(
        active_grace_seconds=600,
        delivery_grace_seconds=600,
    ),
)
print(counts["tasks_timed_out"], counts["deliveries_failed"])
```

Reconciliation runs only when the caller invokes it; there is no background
watcher. It never changes a queued task into success, never retries work, and
never claims that a transport completed. A stale running task is closed as
`timed_out`. A terminal delivery still lacking acknowledgement remains pending
during the configured grace window, then becomes `failed` when delivery is
required or `not_applicable` for internal work. This is durable bookkeeping, not
exactly-once execution or exactly-once delivery.

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
python3.12 scripts/install_smoke.py
python3.12 scripts/artifact_smoke.py dist
```

The last two commands run after installing the checkout or building wheel and
source-distribution artifacts, respectively. `artifact_smoke.py` rejects unsafe
archive members, audits the extracted source distribution, installs the wheel
offline into a fresh virtual environment, and runs the installed CLI lifecycle.

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
audit or establish a Windows DACL. A separate Python 3.14 package matrix builds,
audits, installs, and smoke-tests wheel and source distributions independently
on Linux, macOS, and Windows.

## FAQ / 常见问题

### Does TaskStateGuard resume an interrupted AI agent task? / 能续跑中断任务吗？

No. It preserves and reconciles lifecycle metadata; it does not checkpoint or
resume computation. A stale running task becomes `timed_out` only after its
deadline or configured grace period. The surrounding runtime decides whether
to create a new retry.

不能。它恢复的是“状态事实”，不是计算过程。是否重新执行必须由外部运行时决定。

### Does it guarantee exactly-once execution or delivery? / 能保证恰好一次吗？

No. It makes repeated identical closes idempotent and rejects conflicting
terminal outcomes, but it cannot observe an external side effect. Mark delivery
as `delivered` only after the transport records a real acknowledgement.

不能。它防止状态被重复或冲突地改写，但外部副作用与真实投递回执仍由调用方负责。

### Is it a Python task queue, scheduler, or workflow engine? / 它是任务队列吗？

No. TaskStateGuard stores a local SQLite state ledger. It does not enqueue,
schedule, execute, cancel, retry, or transmit work. It can sit beside an agent
runtime, worker pool, or workflow system that owns those responsibilities; this
repository does not ship framework-specific adapters.

### Why model task status and delivery status separately? / 为什么拆成两套状态？

A task may finish successfully while its result is still pending, and an
internal child task may require no direct user-facing delivery. Keeping the two
state machines separate prevents a completed task from being mistaken for a
confirmed delivery.

### Does it store prompts, messages, or model output? / 会保存提示词和消息吗？

No. Those values have no supported column or CLI argument. The optional
SHA-256 payload fingerprint is correlatable pseudonymous metadata, not anonymous
content, and should be omitted or keyed externally when offline guessing is a
concern.

### What is required on Windows, macOS, and Linux? / 各平台有什么要求？

Use a local SQLite-compatible filesystem on every platform. Linux and macOS use
owner/mode checks and `0600` database files. Windows requires a pre-created
service-account directory with a private DACL plus `allow_external_acl=True`;
the standard library cannot verify that DACL. Network and mapped drives are not
supported.

## Project status

This pre-release clean-room implementation is licensed under the Apache License,
Version 2.0; see `LICENSE`. See `PROVENANCE.md` for the clean-room and human
review record, `CONTRIBUTING.md` for contribution guidance, `SECURITY.md` for
reporting guidance, `SUPPORT.md` for support boundaries, `CHANGELOG.md` for
release notes, `CODE_OF_CONDUCT.md` for community standards,
`RELEASING.md` for the maintainer release checklist, and `THREAT_MODEL.md`
for the exact privacy boundary and non-goals.
