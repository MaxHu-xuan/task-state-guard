# TaskStateGuard

任务状态守护

[![CI](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml)

[中文介绍](#中文介绍) · [中文常见问题](#中文常见问题) · [English overview](#english-overview) · [Technical reference](#technical-reference) · [English FAQ](#english-faq)

## 中文介绍

TaskStateGuard 是一个面向 AI 智能体和后台 worker 的重启安全任务状态账本。它把任务
执行状态与结果投递状态分别写入本地 SQLite，在进程崩溃或重启后收敛卡住的状态，
同时不保存提示词、消息正文或任务正文。

### 适用场景

| 场景 | 使用后得到的结果 |
|---|---|
| 智能体或 worker 重启，部分任务仍显示运行中 | 超过截止时间或活跃宽限期的运行中任务会如实转为 `timed_out`，新鲜任务继续保留 |
| 任务已经结束，但用户侧没有真实投递回执 | 投递状态在宽限期内保持 `pending`，到期后按任务类型转为 `failed` 或 `not_applicable` |
| 运维需要判断 SQLite 任务账本是否一致 | `doctor` 返回完整性、状态和事件聚合计数，不输出任务 ID 或负载指纹 |
| 同一套任务状态逻辑需要运行在 Linux、macOS 和 Windows | Linux 和 macOS 使用文件权限保护；Windows 使用调用方预先配置的私有 DACL 边界 |

### 三步使用

#### 1. 从已审查的源码安装

Linux 或 macOS：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

#### 2. 只记录真实发生的状态变化

```python
import os

from task_state_guard import Ledger

ledger = Ledger(
    "task-state-guard.sqlite",
    allow_external_acl=os.name == "nt",
)
task = ledger.create_task(timeout_seconds=900)
ledger.start_task(task.id)
ledger.close_task(task.id, "succeeded", code="worker_completed")
ledger.set_delivery(task.id, "delivered", code="transport_acknowledged")
```

#### 3. 重启后执行收敛和诊断

```bash
task-state-guard --db ./task-state-guard.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600
task-state-guard --db ./task-state-guard.sqlite doctor
```

Windows 使用前必须由管理员或部署系统创建仅服务账户可访问的目录，并在全局参数位置
加入 `--allow-external-acl`。该参数只确认外部 ACL 已配置，不会创建或认证 DACL。

### 使用结果

- 重启后的 stale task reconciliation 有明确宽限期，不会把失联任务猜成成功。
- 任务完成与结果送达分别记录，未收到 transport acknowledgement 就不会标记已投递。
- SQLite WAL、事务、外键、schema fingerprint 和 `quick_check` 共同保护账本一致性。
- 诊断输出以计数为主；数据库没有提示词、消息正文或自由文本错误字段。
- 运行时代码只使用 Python 标准库，不包含网络请求或遥测。

### 使用限制

- 它不是任务队列、调度器、worker、重试服务、工作流引擎或消息传输层。
- 它不会恢复中断的计算，也不保证 exactly-once execution 或 exactly-once delivery。
- 外部系统必须执行真实工作，并在收到真实投递回执后更新 delivery state。
- 数据库必须位于支持稳定文件身份和 SQLite 锁的本地文件系统；不支持网络盘和映射盘。
- Windows 的 DACL 由外部系统负责，Python 标准库无法验证其等价于 POSIX `0600`。

详细状态机、存储边界、诊断规则和测试矩阵见后面的英文技术参考。

## 中文常见问题

### TaskStateGuard 能续跑中断的智能体任务吗？

不能。它保存并收敛任务生命周期元数据，不会保存计算检查点或恢复计算。运行中任务只会
在超过截止时间或配置的宽限期后转为 `timed_out`，是否创建新的重试任务由外部运行时
决定。

### 它能保证恰好一次执行或恰好一次投递吗？

不能。重复写入同一个终态是幂等的，冲突终态会被拒绝，但它无法观察外部副作用。
只有传输系统记录了真实回执后，调用方才能把 delivery state 更新为 `delivered`。

### 它是 Python 任务队列、调度器或工作流引擎吗？

不是。TaskStateGuard 只保存本地 SQLite 状态账本，不负责入队、调度、执行、取消、
重试或发送任务。它可以与智能体运行时、worker pool 或工作流系统配合使用，但当前
仓库不提供框架专用适配器。

### 为什么任务状态和投递状态要分开？

任务可能已经成功结束，但结果仍在等待投递；内部子任务也可能不需要直接面向用户的
投递。拆分两套状态机可以避免把任务完成误判为结果已经送达。

### 它会保存提示词、消息或模型输出吗？

不会。数据库和 CLI 都没有这些字段。可选的 SHA-256 payload fingerprint 属于可关联的
假名化元数据，并不是匿名内容；如果低熵输入可能被离线猜测，应省略该值或在外部使用
带密钥的指纹方案。

### Windows、macOS 和 Linux 分别有什么要求？

所有平台都必须使用支持 SQLite 锁的本地文件系统。Linux 和 macOS 使用所有者与权限
检查，并把数据库文件设为 `0600`。Windows 需要预先创建带私有 DACL 的服务账户目录，
再传入 `allow_external_acl=True`；Python 标准库无法验证该 DACL。网络盘和映射盘不受
支持。

## English Overview

TaskStateGuard is a restart-safe SQLite task-state and delivery ledger for AI
agent runtimes and background workers. It reconciles stale state after a crash
or process restart without storing prompts, messages, or task bodies and without
inventing successful work or delivery.

### Use cases

| Situation | Outcome |
|---|---|
| An agent or worker restarts while tasks still look active | Running tasks past their deadline or active grace become `timed_out`; fresh work is retained |
| Work is terminal but no real delivery acknowledgement exists | Delivery stays `pending` during grace, then becomes `failed` or `not_applicable` according to task type |
| An operator needs to validate a SQLite task ledger | `doctor` reports aggregate integrity, state, and event counts without task IDs or payload fingerprints |
| One state model must run on Linux, macOS, and Windows | POSIX modes protect Linux/macOS files; Windows uses an explicit externally managed private DACL boundary |

### Three-step setup

#### 1. Install from a reviewed checkout

Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

#### 2. Record real state transitions

```python
import os

from task_state_guard import Ledger

ledger = Ledger(
    "task-state-guard.sqlite",
    allow_external_acl=os.name == "nt",
)
task = ledger.create_task(timeout_seconds=900)
ledger.start_task(task.id)
ledger.close_task(task.id, "succeeded", code="worker_completed")
ledger.set_delivery(task.id, "delivered", code="transport_acknowledged")
```

#### 3. Reconcile and diagnose after restart

```bash
task-state-guard --db ./task-state-guard.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600
task-state-guard --db ./task-state-guard.sqlite doctor
```

On Windows, first create a service-account directory protected by a private
DACL, then place `--allow-external-acl` with the global CLI arguments. The flag
acknowledges an external boundary; it does not create or certify the DACL.

The Python API exposes `Ledger`, `ReconcilePolicy`, and `reconcile_restart` for
embedded runtimes. The CLI provides the same lifecycle, reconciliation, and
counts-only diagnostic operations for process-based integrations.

### Outcomes

- Restart-safe agent recovery closes stale running state after explicit grace.
- Task completion and result delivery remain separate facts.
- Atomic SQLite writes, WAL, foreign keys, schema fingerprinting, and
  `quick_check` protect local ledger consistency.
- The schema has no plaintext prompt, message, task body, path, or free-form
  exception field.
- Runtime code uses only the Python standard library and has no network or
  telemetry path.

### Limits

- TaskStateGuard is not a queue, scheduler, worker, retry service, workflow
  engine, process supervisor, or transport.
- It does not resume computation or guarantee exactly-once execution or
  exactly-once delivery.
- The surrounding runtime must perform work and record a real transport
  acknowledgement before delivery becomes `delivered`.
- Use a local SQLite-compatible filesystem. Network and mapped drives are not
  supported.
- Windows privacy depends on a caller-managed DACL that the Python standard
  library cannot inspect or certify.

## Technical reference

The remaining technical reference is written in English.

### Why it exists

Agent runtimes often have two different truths:

1. whether work finished; and
2. whether its result was delivered.

Conflating them produces misleading states such as a timed-out task that remains
"pending" forever, or a successful internal child task that appears to require a
user-facing delivery. TaskStateGuard models both state machines explicitly and
reconciles stale work after a restart with configurable grace periods.

### Properties

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

### State model

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

### Detailed installation

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

### Source and Python examples

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

### Platform contract

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

### CLI output and privacy contract

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

### Storage boundary

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
user identity are trusted; see [THREAT_MODEL.md](THREAT_MODEL.md) for the
remaining limits.

### Restart reconciliation

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

### Doctor and migration

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

### Tests

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

## English FAQ

### Does TaskStateGuard resume an interrupted AI agent task?

No. It preserves and reconciles lifecycle metadata; it does not checkpoint or
resume computation. A stale running task becomes `timed_out` only after its
deadline or configured grace period. The surrounding runtime decides whether
to create a new retry.

### Does it guarantee exactly-once execution or delivery?

No. It makes repeated identical closes idempotent and rejects conflicting
terminal outcomes, but it cannot observe an external side effect. Mark delivery
as `delivered` only after the transport records a real acknowledgement.

### Is it a Python task queue, scheduler, or workflow engine?

No. TaskStateGuard stores a local SQLite state ledger. It does not enqueue,
schedule, execute, cancel, retry, or transmit work. It can sit beside an agent
runtime, worker pool, or workflow system that owns those responsibilities; this
repository does not ship framework-specific adapters.

### Why model task status and delivery status separately?

A task may finish successfully while its result is still pending, and an
internal child task may require no direct user-facing delivery. Keeping the two
state machines separate prevents a completed task from being mistaken for a
confirmed delivery.

### Does it store prompts, messages, or model output?

No. Those values have no supported column or CLI argument. The optional
SHA-256 payload fingerprint is correlatable pseudonymous metadata, not anonymous
content, and should be omitted or keyed externally when offline guessing is a
concern.

### What is required on Windows, macOS, and Linux?

Use a local SQLite-compatible filesystem on every platform. Linux and macOS use
owner/mode checks and `0600` database files. Windows requires a pre-created
service-account directory with a private DACL plus `allow_external_acl=True`;
the standard library cannot verify that DACL. Network and mapped drives are not
supported.

## Project status

This pre-release clean-room implementation is licensed under the Apache License,
Version 2.0; see [LICENSE](LICENSE). See [PROVENANCE.md](PROVENANCE.md) for the
clean-room and human review record, [CONTRIBUTING.md](CONTRIBUTING.md) for
contribution guidance, [SECURITY.md](SECURITY.md) for reporting guidance,
[SUPPORT.md](SUPPORT.md) for support boundaries, [CHANGELOG.md](CHANGELOG.md)
for release notes, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community
standards, [RELEASING.md](RELEASING.md) for the maintainer release checklist,
and [THREAT_MODEL.md](THREAT_MODEL.md) for the exact privacy boundary and
non-goals.
