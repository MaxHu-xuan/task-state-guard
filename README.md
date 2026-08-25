# TaskStateGuard（任务状态守护）

[![CI](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxHu-xuan/task-state-guard/actions/workflows/ci.yml)

[中文](#中文) · [English](#english) · [运行合成演示](#2-运行完全合成的演示) ·
[Technical reference](#technical-reference)

## 中文

服务重启后，`running` 任务和 `pending` 投递很容易继续留在界面里，却没人能确认它们
是否真的还在工作。TaskStateGuard 是面向 AI 智能体、后台 worker 和本地工作流的
嵌入式 SQLite 状态对账护栏。它把任务终态与投递终态分开，按明确的截止时间和
宽限期收敛遗留状态；你可以先看只含汇总数字的预览，再决定是否应用，`doctor`
健康检查同样不会列出任务明细。

它解决的是任务状态对账（task state reconciliation）和重启后卡住任务的状态收敛
（stuck task recovery）。它不是队列、执行器或计算续跑工具，也不承诺恰好一次；系统没有
观测到成功或投递回执时，TaskStateGuard 不会把未知结果猜成成功或已送达。

### 三个工具怎么选

| 你需要解决的问题 | 选择 |
|---|---|
| 重启后核对卡住任务、超时与待投递状态，不把未知结果猜成成功 | TaskStateGuard（任务状态守护），当前项目 |
| 分享或迁移聊天导出前，本地检查疑似秘密、个人信息形态、格式、SQLite 与扫描盲区 | [ChatArchiveGuard（聊天归档守护）](https://github.com/MaxHu-xuan/chat-archive-guard) |
| 确认最终 PPTX 及随附验收证据仍匹配结构检查后生成的 HMAC 签名收据 | [ArtifactProof（PPTX 交付物验真）](https://github.com/MaxHu-xuan/artifactproof) |

三个项目都可以独立使用；它们分别守护状态、聊天归档和 PPTX 交付物，不会互相读取数据。

### 它能带来什么

| 你遇到的问题 | TaskStateGuard 给出的结果 |
|---|---|
| 服务重启后，一批任务一直显示 `running` | 超过截止时间或活跃宽限期的任务转为 `timed_out`；仍然新鲜的任务保持不变 |
| 任务已经结束，但用户是否收到结果并不确定 | 投递状态继续保持 `pending`，直到真实回执或投递宽限期到期 |
| 内部子任务完成后不应单独向用户投递 | 终态内部任务在宽限期后转为 `not_applicable`，不会伪装成已送达 |
| 收敛遗留状态前需要知道会改动多少记录 | `reconcile --dry-run` 返回同一时刻将发生的聚合计数，不修改任务、事件或数据库 |
| 运维需要判断工作流账本是否可信 | `doctor` 检查数据库、schema、状态、时间戳和事件链，只返回聚合计数 |
| Linux、macOS 和 Windows 需要共用一套状态语义 | 状态机一致；文件保护按 POSIX 权限或外部管理的 Windows DACL 分别处理 |

适合以下场景：

- AI 智能体运行时在重启后需要识别失联任务；
- worker 服务需要区分任务终态和投递状态；
- 本地工作流需要处理超时与重启，并使用可审计的宽限期；
- 运维希望增加工作流可观测性（workflow observability），但不想把提示词或任务正文
  写入诊断账本。

### 状态模型

任务状态：

```text
queued ──> running
  │           │
  └───────────┴──> succeeded | failed | timed_out | cancelled
```

任务可以从 `queued` 或 `running` 进入终态。终态不可改写；重复写入相同终态是幂等的，
写入冲突终态会被拒绝。

投递状态：

```text
pending ──> delivered | failed | not_applicable
```

投递状态只能在任务进入终态后关闭。外部投递任务只有收到真实传输回执后才能标记为
`delivered`；无需直接投递的内部任务只能标记为 `not_applicable`。

这两套状态机回答不同问题：

- 任务终态回答工作是否结束、以什么结果结束；
- 投递状态回答结果是否真实送达，或是否根本不需要直接送达。

### 快速开始

#### 1. 安装 TaskStateGuard

对于已经发布到 PyPI 的版本，普通用户建议在虚拟环境中安装 TaskStateGuard。

Linux 或 macOS：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install task-state-guard
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install task-state-guard
```

如需审阅尚未发布的修改或参与开发，请先确认源码来源和目标提交，再在已核对的源码仓库
根目录创建独立虚拟环境，并将最后一条安装命令改为 `python -m pip install .`
（Windows 使用 `.\.venv\Scripts\python.exe -m pip install .`）。

支持 CPython 3.11 至 3.14。运行时代码只使用 Python 标准库，不包含网络请求或遥测。

#### 2. 运行完全合成的演示

演示脚本随源码仓库提供，不包含在 wheel 中。请从仓库根目录运行；它只使用固定的公开
样例状态，在系统临时目录创建 SQLite，调用真实 CLI 完成
preview、apply、幂等复查和 `doctor`，结束后自动删除临时数据库。它不读取本机任务数据，
也不包含提示词、消息、任务正文或真实标识。

Linux 或 macOS：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/restart_reconciliation_demo.py
```

Windows PowerShell：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
py -3 .\examples\restart_reconciliation_demo.py --allow-external-acl
```

真实演示中的前三个聚合结果如下；`<temporary-demo>` 是刻意使用的、不包含本机信息的
路径占位符：

```text
[preview] output: {"ok":true,"reconcile":{"active_examined":2,"applied":false,"deliveries_failed":1,"deliveries_not_applicable":1,"dry_run":true,"fresh_active_retained":1,"pending_delivery_examined":3,"pending_delivery_retained":1,"tasks_timed_out":1}}
[apply] output: {"ok":true,"reconcile":{"active_examined":2,"applied":true,"deliveries_failed":1,"deliveries_not_applicable":1,"dry_run":false,"fresh_active_retained":1,"pending_delivery_examined":3,"pending_delivery_retained":1,"tasks_timed_out":1}}
[idempotent-recheck] output: {"ok":true,"reconcile":{"active_examined":1,"applied":true,"deliveries_failed":0,"deliveries_not_applicable":0,"dry_run":false,"fresh_active_retained":1,"pending_delivery_examined":1,"pending_delivery_retained":1,"tasks_timed_out":0}}
```

第三次没有再次改写已经闭合的记录；仍保留的 `queued` 和刚刚进入 `timed_out` 的投递解释了
两个 retained 计数。完整命令与只含汇总数字的 `doctor` 输出由脚本直接打印，并在测试中
连续运行两次验证一致性。

#### 3. 只记录真实发生的状态变化

```python
from task_state_guard import Ledger

ledger = Ledger("./private-state/tasks.sqlite")
task = ledger.create_task(timeout_seconds=900)
ledger.start_task(task.id)

# 外部 worker 确认工作成功后再写入任务终态。
ledger.close_task(task.id, "succeeded", code="worker_completed")

# 传输层收到真实回执后再写入投递终态。
ledger.set_delivery(
    task.id,
    "delivered",
    code="transport_acknowledged",
)
```

在 Windows 上，数据库父目录必须提前存在，并由管理员或部署系统设置为仅服务账户可访问的
私有 DACL。只有确认该边界已经建立后，才传入 `allow_external_acl=True`。

#### 4. 先预览，再执行重启收敛

```bash
task-state-guard --db ./private-state/tasks.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600 \
  --dry-run
task-state-guard --db ./private-state/tasks.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600
task-state-guard --db ./private-state/tasks.sqlite doctor
```

`--dry-run` 使用与实际执行相同的判断规则，但不会修改任务、事件或数据库。时间会继续前进，
并发 worker 也可能更新状态，因此预览只代表预览时刻；执行前应核对返回的 `dry_run` 和
`applied` 字段。

CLI 的 dry-run 会以只读模式打开一个已经存在、已经采用 WAL 且权限边界合格的账本；它
不会创建数据库、收紧文件权限或迁移 schema。安全的 schema v1 可以直接预览并保持为
v1；普通执行仍按原有默认行为迁移到 v2。嵌入 Python 时，如果连构造阶段也必须只读，请
使用 `Ledger(path, read_only=True)`，并只调用读取操作或 `dry_run=True` 的收敛。

数据库已干净关闭且没有 WAL/SHM 时，TaskStateGuard 会对不超过 64 MiB 的主库做两次
身份、时间和 SHA-256 一致性读取，再把私有副本载入内存；不会用 SQLite 的 immutable
模式直接读取可能并发变化的源文件，也不会创建临时副本。读取期间出现 writer 或
checkpoint、内存反序列化不可用、或超过上限时都会安全失败。已有完整 WAL/SHM 时仍使用
SQLite 的正常只读锁语义。

Windows CLI 需要把 DACL 确认参数放在子命令之前：

```powershell
.\.venv\Scripts\task-state-guard.exe `
  --db .\private-state\tasks.sqlite `
  --allow-external-acl `
  reconcile --active-grace-seconds 600 --delivery-grace-seconds 600 --dry-run
```

`--allow-external-acl` 只确认调用方已配置私有 DACL，不会创建、检查或认证 DACL。

### Linux、macOS 与 Windows 命令差异

| 平台 | 源码与虚拟环境命令 | 文件保护与 CLI 注意事项 |
|---|---|---|
| Linux | 通常使用 `python3`、`. .venv/bin/activate`、`PYTHONPATH=src` 和 `/` 路径 | 项目检查并收紧 POSIX 文件模式；数据库必须在本地文件系统 |
| macOS | 与 Linux 使用相同的 shell 写法和 `/` 路径 | 同样执行 POSIX 模式与链接检查；数据库必须在本地文件系统 |
| Windows PowerShell | 使用 `py -3`、`$env:PYTHONPATH = "src"` 和 `\` 路径 | 先由外部配置私有 DACL，并把 `--allow-external-acl` 放在子命令之前 |

三个平台的任务与投递状态语义相同；差异只在命令写法和本地文件保护边界。在 Windows
上，合成演示只有收到显式参数后才会加入 DACL 确认，但仍不会替你配置或验证 DACL。
运行前请先确认临时目录的 DACL 边界，并确认上述 `python3` 或 `py -3` 实际选择的是
受支持的 CPython 3.11 至 3.14。

### 工作流可观测性

`reconcile` 返回本次将收敛或已收敛的聚合计数，不返回任务 ID。输出中的
`dry_run=true, applied=false` 表示只读预览；默认执行返回
`dry_run=false, applied=true`。`doctor` 检查：

- SQLite `quick_check`、外键和精确 schema；
- 状态与投递语义是否一致；
- 父子任务顺序、环路、UUID 和有限时间戳；
- 每个任务的预期事件链是否完整。

`doctor` 健康时退出码为 `0`。只要当前数据库中真实存在结构、状态、时间戳或事件链
不一致，它就返回 `1`；已闭合且一致的历史终态不会仅因仍被保留而失败。它也不会因为
报告能正常生成就把不健康账本当作成功。普通状态命令会返回操作所需的 UUID、状态和
时间戳，因此这些输出仍应像数据库一样受到保护。

数据库没有提示词、消息正文、任务正文、路径或自由文本异常字段。可选的 SHA-256
payload fingerprint 是可关联的假名化元数据，不是匿名数据；低熵内容可能被离线猜测时，
应省略它或在 TaskStateGuard 之外使用带密钥的指纹方案。

### 真实边界

- TaskStateGuard 不是队列、调度器、worker、重试服务、工作流引擎、进程监管器或传输层。
- 它不会续跑中断的任务，也不保证恰好一次执行或恰好一次投递。
- 收敛只在调用方执行 `reconcile` 时发生；项目没有后台 watcher。
- 只有真实 worker 或传输系统能确认成功与投递，账本不会推断外部副作用。
- `--dry-run` 不是锁或事务预约；预览与随后执行之间，时间和并发状态可能变化。
- 数据库必须位于支持稳定文件身份和 SQLite 锁的本地文件系统；不支持网络盘、映射盘和
  远程挂载。
- Linux 和 macOS 使用目录/文件模式与链接检查；Windows 的私有 DACL 由外部系统负责，
  Python 标准库无法验证它是否等价于 POSIX `0600`。
- 同一操作系统用户身份下的其他进程被视为可信；完整威胁边界见
  [THREAT_MODEL.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/THREAT_MODEL.md)。

### 常见问题

#### TaskStateGuard 能续跑中断的智能体任务吗？

不能。它保存并收敛任务生命周期元数据，不保存计算检查点，也不恢复计算。超过截止时间
或活跃宽限期的 `running` 任务会转为 `timed_out`；是否新建重试任务由外部运行时决定。

#### 它能保证恰好一次执行或恰好一次投递吗？

不能。相同终态的重复写入是幂等的，冲突终态会被拒绝，但 TaskStateGuard 无法观察外部
副作用。只有传输系统记录了真实回执后，调用方才能把投递状态更新为 `delivered`。

#### 它是 Python 任务队列、调度器或工作流引擎吗？

不是。它只保存本地 SQLite 状态账本，不负责入队、调度、执行、取消、重试或发送任务。
它可以放在 agent runtime、worker pool 或工作流系统旁边，但当前仓库不提供框架专用
适配器。

#### 为什么任务状态和投递状态要分开？

任务可能已经成功，但结果仍在等待投递；内部子任务也可能不需要直接面向用户投递。
拆分状态机可以避免把任务完成误判为结果已经送达。

#### 预览结果是否保证与稍后执行完全相同？

只有在使用同一时刻且期间没有并发状态变化时才相同。`--dry-run` 不写数据库，也不锁定
未来执行；它用于审阅当前判断，不是对后续执行的预约。

#### 它会保存提示词、消息或模型输出吗？

不会，这些值没有受支持的数据库字段或 CLI 参数。可选的 SHA-256 fingerprint 仍可能被
关联或猜测，因此只应在理解其隐私边界时使用。

#### Windows、macOS 和 Linux 分别有什么要求？

所有平台都必须使用支持 SQLite 锁的本地文件系统。Linux 和 macOS 使用所有者/模式检查
并把数据库及 sidecar 收紧为 `0600`。Windows 要求预先创建带私有 DACL 的服务账户目录，
再显式传入 `allow_external_acl=True`；项目不能替你验证 DACL。

## English

After a service restarts, `running` tasks and `pending` deliveries can remain
visible even when no one can confirm that they are still active. TaskStateGuard
is an embedded SQLite reconciliation guardrail for AI agents, background workers,
and local workflow runtimes. It keeps task terminal state separate from delivery
terminal state and reconciles stale state using explicit deadlines and grace
periods. You can preview aggregate changes before applying them, then use a
counts-only `doctor` to check the ledger.

It provides task-state reconciliation and stuck-task recovery for metadata. It
is not a queue, executor, or computation-resumption tool and makes no exactly-once
guarantee. When the surrounding system has not observed success or a delivery
acknowledgement, TaskStateGuard does not guess that the work succeeded or was
delivered.

### What it gives you

| Problem | Result |
|---|---|
| A service restarts while tasks remain `running` | Tasks past their deadline or active grace become `timed_out`; fresh tasks remain unchanged |
| Work is terminal but user-facing delivery is uncertain | The `delivery state` stays `pending` until a real acknowledgement or the delivery grace expires |
| An internal child task should not create its own delivery obligation | The terminal internal task becomes `not_applicable` after grace instead of pretending it was delivered |
| You need to see the impact before applying reconciliation | `reconcile --dry-run` returns the aggregate changes for that moment without updating tasks, events, or the database |
| Operations needs to decide whether the ledger is trustworthy | `doctor` checks the database, schema, state, timestamps, and event chains and emits aggregate counts |
| Linux, macOS, and Windows need one state contract | The state model is portable; storage protection uses POSIX modes or a caller-managed Windows DACL |

TaskStateGuard fits runtimes that need:

- restart recovery for agent or worker state;
- a clear boundary between terminal state and delivery state;
- explicit timeout recovery without guessing that work succeeded;
- workflow observability without storing prompts or task bodies.

### Choose among the three tools

| Problem to solve | Choose |
|---|---|
| After a restart, reconcile stuck tasks, timeouts, and pending delivery without guessing success | TaskStateGuard, this project |
| Before sharing or migrating a chat export, locally audit potential secrets, personal-data patterns, format or SQLite issues, and scan gaps | [ChatArchiveGuard](https://github.com/MaxHu-xuan/chat-archive-guard) |
| Verify that a final PPTX and its supplied QA evidence still match the HMAC-signed receipt created after structural checks | [ArtifactProof](https://github.com/MaxHu-xuan/artifactproof) |

Each project works independently. They protect state, chat archives, and PPTX
artifacts respectively, without reading one another's data.

### State model

Task states:

```text
queued ──> running
  │           │
  └───────────┴──> succeeded | failed | timed_out | cancelled
```

A task can close from `queued` or `running`. Terminal states are immutable.
Repeating the same close is idempotent; a conflicting close is rejected.

Delivery states:

```text
pending ──> delivered | failed | not_applicable
```

Delivery can close only after the task is terminal. A delivery-required task
becomes `delivered` only after a real transport acknowledgement. An internal
task that has no direct delivery obligation can become only `not_applicable`.

The two state machines answer different questions:

- terminal state says whether and how the work ended;
- delivery state says whether the result was actually delivered or did not need
  direct delivery.

### Quick start

#### 1. Install TaskStateGuard

After a release is published to PyPI, most users should install TaskStateGuard
in a virtual environment.

Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install task-state-guard
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install task-state-guard
```

To review unreleased changes or contribute, first verify the source and target
commit. Then create an isolated environment from that checkout's repository root
and replace the final command with `python -m pip install .` (on Windows, use
`.\.venv\Scripts\python.exe -m pip install .`).

TaskStateGuard supports CPython 3.11 through 3.14. Runtime code uses only the
Python standard library and has no network or telemetry path.

#### 2. Run the fully synthetic demonstration

The demo script is provided in the source repository and is not included in the
wheel. Run it from the repository root. It creates a SQLite database under the
system temporary directory from fixed public sample states, invokes the real CLI
for preview, apply, an idempotent recheck, and `doctor`, then removes the
database. It does not read local task data or contain prompts, messages, task
bodies, or real identifiers.

Linux or macOS:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/restart_reconciliation_demo.py
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
py -3 .\examples\restart_reconciliation_demo.py --allow-external-acl
```

The first three aggregate results from the real demonstration are shown below.
`<temporary-demo>` is an intentional non-identifying path placeholder:

```text
[preview] output: {"ok":true,"reconcile":{"active_examined":2,"applied":false,"deliveries_failed":1,"deliveries_not_applicable":1,"dry_run":true,"fresh_active_retained":1,"pending_delivery_examined":3,"pending_delivery_retained":1,"tasks_timed_out":1}}
[apply] output: {"ok":true,"reconcile":{"active_examined":2,"applied":true,"deliveries_failed":1,"deliveries_not_applicable":1,"dry_run":false,"fresh_active_retained":1,"pending_delivery_examined":3,"pending_delivery_retained":1,"tasks_timed_out":1}}
[idempotent-recheck] output: {"ok":true,"reconcile":{"active_examined":1,"applied":true,"deliveries_failed":0,"deliveries_not_applicable":0,"dry_run":false,"fresh_active_retained":1,"pending_delivery_examined":1,"pending_delivery_retained":1,"tasks_timed_out":0}}
```

The third pass does not rewrite already closed records. The retained queued
task and the newly timed-out task's delivery account for the remaining counts.
The script prints every command and the full counts-only `doctor` report; the
test suite runs it twice and requires byte-identical output.

#### 3. Record only observed transitions

```python
from task_state_guard import Ledger

ledger = Ledger("./private-state/tasks.sqlite")
task = ledger.create_task(timeout_seconds=900)
ledger.start_task(task.id)

# Close the task only after the worker confirms the outcome.
ledger.close_task(task.id, "succeeded", code="worker_completed")

# Close delivery only after the transport records an acknowledgement.
ledger.set_delivery(
    task.id,
    "delivered",
    code="transport_acknowledged",
)
```

On Windows, the database parent directory must already exist with a private DACL
restricted to the service account. Pass `allow_external_acl=True` only after an
administrator or deployment system has established that boundary.

#### 4. Preview, then apply restart reconciliation

```bash
task-state-guard --db ./private-state/tasks.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600 \
  --dry-run
task-state-guard --db ./private-state/tasks.sqlite reconcile \
  --active-grace-seconds 600 \
  --delivery-grace-seconds 600
task-state-guard --db ./private-state/tasks.sqlite doctor
```

`--dry-run` uses the same decision rules as apply mode without updating tasks,
events, or the database. Time can advance and concurrent workers can change
state, so a preview describes that moment rather than reserving a later result.
Check the `dry_run` and `applied` fields before acting on the output.

The CLI dry-run opens an existing, WAL-enabled ledger through a read-only
connection. It does not create the database, tighten file permissions, or
migrate its schema. A safe schema-v1 ledger can be previewed and remains v1;
ordinary apply mode keeps the existing default migration to v2. Embedded Python
callers that need the construction step to be read-only should use
`Ledger(path, read_only=True)` and then call only read operations or reconciliation
with `dry_run=True`.

For a cleanly closed database with no WAL/SHM pair, TaskStateGuard performs two
matching identity, timestamp, and SHA-256 reads of the main database, up to
64 MiB, before loading a private copy into memory. It never points SQLite
immutable mode at a source file that a concurrent writer could change, and it
creates no temporary copy. A writer or checkpoint during capture, unavailable
deserialization support, or an oversized source fails closed. An existing
complete WAL/SHM pair continues to use SQLite's normal read-only locking.

For the Windows CLI, place the DACL acknowledgement before the subcommand:

```powershell
.\.venv\Scripts\task-state-guard.exe `
  --db .\private-state\tasks.sqlite `
  --allow-external-acl `
  reconcile --active-grace-seconds 600 --delivery-grace-seconds 600 --dry-run
```

`--allow-external-acl` acknowledges a boundary managed by the caller. It does
not create, inspect, or certify a Windows DACL.

### Linux, macOS, and Windows command differences

| Platform | Source and environment syntax | Storage and CLI note |
|---|---|---|
| Linux | Typically `python3`, `. .venv/bin/activate`, `PYTHONPATH=src`, and `/` paths | TaskStateGuard checks and tightens POSIX modes; keep the database on a local filesystem |
| macOS | The same shell syntax and `/` paths as Linux | The same POSIX mode and link checks apply; keep the database on a local filesystem |
| Windows PowerShell | `py -3`, `$env:PYTHONPATH = "src"`, and `\` paths | Configure a private DACL externally and place `--allow-external-acl` before the subcommand |

Task and delivery semantics are identical on all three platforms. Only command
syntax and local storage protection differ. On Windows, the synthetic demo adds
the DACL acknowledgement only after receiving its explicit command-line flag;
it does not configure or verify a DACL. Confirm the temporary directory's DACL
boundary first. Also confirm that `python3` or `py -3` selects a supported
CPython 3.11 through 3.14 interpreter.

### Workflow observability

`reconcile` returns aggregate counts for the changes it would make or did make
and never returns task IDs. `dry_run=true, applied=false` identifies a read-only
preview; the default apply mode returns `dry_run=false, applied=true`. `doctor`
checks:

- SQLite `quick_check`, foreign keys, and the exact schema;
- task and delivery-state semantics;
- parent ordering, cycles, UUIDs, and finite timestamp order;
- the complete expected event chain for every task.

A healthy `doctor` exits with status `0`. It exits with status `1` whenever the
current database contains a real structural, state, timestamp, or event-chain
inconsistency. Consistent retained terminal history does not fail merely because
it is old. Ordinary state commands return operational UUIDs, states, and
timestamps, so protect their output as you would the database.

The schema has no prompt, message body, task body, path, or free-form exception
field. The optional SHA-256 payload fingerprint is correlatable pseudonymous
metadata, not anonymous data. Omit it, or use an externally keyed construction,
when low-entropy content could be guessed offline.

### Real boundaries

- TaskStateGuard is not a queue, scheduler, worker, retry service, workflow
  engine, process supervisor, or transport.
- It does not resume computation or guarantee exactly-once execution or
  exactly-once delivery.
- Reconciliation runs only when the caller invokes `reconcile`; there is no
  background watcher.
- Only the real worker or transport can confirm external side effects.
- `--dry-run` is not a lock or transaction reservation. Time and concurrent
  state may change between preview and apply.
- The database must be on a local filesystem with stable file identity and
  SQLite-compatible locking. Network drives, mapped drives, and remote mounts
  are unsupported.
- Linux and macOS use mode and link checks. Windows privacy relies on a private
  DACL managed by the caller; the Python standard library cannot verify that it
  is equivalent to POSIX `0600`.
- Other processes with the same OS user identity are trusted. See
  [THREAT_MODEL.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/THREAT_MODEL.md)
  for the full boundary.

### FAQ

#### Does TaskStateGuard resume an interrupted AI agent task?

No. It preserves and reconciles lifecycle metadata; it does not store a compute
checkpoint or resume computation. A `running` task becomes `timed_out` only
after its deadline or active grace. The surrounding runtime decides whether to
create a retry.

#### Does it guarantee exactly-once execution or delivery?

No. Identical terminal writes are idempotent and conflicting terminal writes
are rejected, but TaskStateGuard cannot observe external side effects. Mark
delivery as `delivered` only after the transport records a real acknowledgement.

#### Is it a Python task queue, scheduler, or workflow engine?

No. It stores a local SQLite state ledger and does not enqueue, schedule,
execute, cancel, retry, or transmit work. It can sit beside an agent runtime,
worker pool, or workflow system, but this repository ships no framework-specific
adapters.

#### Why separate task status from delivery status?

Work may succeed while its result is still waiting for delivery, and an internal
child task may have no direct user-facing delivery. Separate state machines
prevent task completion from being mistaken for confirmed delivery.

#### Is a preview guaranteed to match a later apply?

Only when both use the same time and no concurrent state changes occur.
`--dry-run` does not write the database or reserve the later transaction; it is
an observation of the current decision set.

#### Does it store prompts, messages, or model output?

No. Those values have no supported database column or CLI argument. The
optional SHA-256 fingerprint remains correlatable and potentially guessable, so
use it only after considering that privacy boundary.

#### What is required on Windows, macOS, and Linux?

Every platform requires a local filesystem with SQLite-compatible locking.
Linux and macOS enforce owner/mode checks and tighten database sidecars to
`0600`. Windows requires a pre-created service-account directory with a private
DACL and explicit `allow_external_acl=True`; TaskStateGuard cannot verify that
DACL for you.

## Technical reference

This section is an English maintainer reference. The complete user-facing
guides and FAQs above remain separated by language.

### Runtime and API contract

- CPython 3.11 through 3.14; no third-party runtime dependencies.
- No network code and no telemetry.
- Atomic SQLite transactions with WAL, foreign keys, `busy_timeout`, and
  `quick_check`.
- Exact schema fingerprinting and a guarded schema-v1 to schema-v2 migration.
- UUID task identifiers and optional opaque SHA-256 payload fingerprints.
- Explicit parent/child relationships and internal, non-deliverable tasks.
- Injected clocks for deterministic recovery tests.

The public Python API exposes `Ledger`, `ReconcilePolicy`, and
`reconcile_restart`. `Ledger` also provides `heartbeat()` for fresh running work,
`children_of()` for direct children, and `storage_info()` for the active storage
permission model. `Ledger(path, read_only=True)` opens an existing ledger without
creating, migrating, or permission-tightening it; mutating methods fail closed.

Reason codes are optional and come from a fixed registry:

- task `succeeded`: `completed`, `worker_completed`;
- task `failed`: `worker_failed`;
- task `timed_out`: `deadline_exceeded`, `restart_stale`;
- task `cancelled`: `caller_cancelled`;
- delivery `delivered`: `transport_acknowledged`;
- delivery `failed`: `delivery_grace_expired`, `transport_failed`;
- delivery `not_applicable`: `internal_task`.

`created` and `started` are reserved for ledger-generated events. A reason code
must match its requested state. Repeating an identical terminal transition does
not replace the original code.

### Storage and platform contract

| Host | Enforced boundary | Required caller action |
|---|---|---|
| Linux | Private owner-controlled leaf; safe writable ancestors; database and SQLite sidecars tightened to `0600`; newly created directories use `0700` | Use a dedicated service-account directory on a local filesystem |
| macOS | The same POSIX mode, ancestor, link, and local-filesystem contract | Use a dedicated service-account directory on a local filesystem |
| Windows | Parent path must already exist; observable reparse points, symlinks, and hardlink aliases are rejected; DACL privacy cannot be inspected by the standard library | Restrict the directory DACL externally, then pass `allow_external_acl=True` or `--allow-external-acl` |

Existing SQLite `-journal`, `-wal`, and `-shm` sidecars are rejected when they
are observable symlinks, Windows reparse points, or have multiple hardlinks. A
database alias of those kinds is also rejected. These checks require stable file
identity and link-count reporting.

Read-only construction requires the database and parent directory to exist, WAL
mode to be active, and POSIX database and sidecar modes to be private already.
It fails closed instead of creating paths, changing journal mode, tightening
permissions, or migrating schema. On Windows, the external private-DACL
acknowledgement remains required.

For a cleanly closed WAL database with no sidecars, two bounded and matching
source reads are required before `sqlite3.Connection.deserialize` loads a
private in-memory snapshot. Only that private copy has its WAL header bytes
normalized for an in-memory database. The source is never opened by SQLite, and
no temporary file is created. When both sidecars already exist, normal read-only
WAL access preserves SQLite locking. A rollback journal, incomplete WAL/SHM
pair, unstable source, source over 64 MiB, or unavailable deserialize support
fails closed without changing the files.

Normal operation creates only the database and SQLite's own sidecars. UNC paths,
mapped network drives, remote mounts, and filesystems with unreliable locking or
identity reporting are unsupported. Not every remote mount can be identified
programmatically, so local-disk deployment remains an operator requirement.

### CLI and privacy contract

Except for `--help` and `--version`, the CLI writes one compact, key-sorted JSON
line to standard output and nothing to standard error. Expected validation or
state errors expose only their exception class. Unexpected exceptions become the
fixed `InternalError` category. Rejected values, paths, and exception messages
are not echoed.

Exit status is deterministic:

- `0`: command succeeded, including a healthy `doctor`;
- `1`: `doctor` completed and found an unhealthy ledger;
- `2`: bounded input, storage, or state error;
- `3`: unexpected internal error.

Successful task commands intentionally return UUIDs, parent UUIDs, states, and
timestamps. Default CLI and `Task.to_dict()` output expose only
`has_payload_hash`, not the optional fingerprint. Trusted in-process callers can
request it with `task.to_dict(include_payload_hash=True)`.

### Restart reconciliation

The reconciler evaluates or applies one atomic transaction:

- a running task past its explicit deadline becomes `timed_out`;
- a running task without a recent heartbeat past active grace becomes
  `timed_out`;
- queued tasks and fresh running tasks stay active;
- a terminal, delivery-required task still pending past delivery grace becomes
  delivery `failed`;
- a terminal internal task still pending past delivery grace becomes
  `not_applicable`.

The report contains counts plus `dry_run` and `applied`, never task IDs. With
`dry_run=True`, SQLite `query_only` mode evaluates the candidate set in a read
transaction and no task, event, or database content is changed. Apply mode uses
the same candidate rules in a write transaction. A preview and later apply can
differ if the clock advances across a threshold or another process updates the
ledger. The read snapshot is fixed before the decision clock is sampled, so a
concurrent heartbeat cannot appear newer than that preview's clock value.

Embedded runtimes can preview and then apply directly:

```python
from task_state_guard import Ledger, ReconcilePolicy, reconcile_restart

preview_ledger = Ledger("./private-state/tasks.sqlite", read_only=True)
policy = ReconcilePolicy(
    active_grace_seconds=600,
    delivery_grace_seconds=600,
)
preview = reconcile_restart(preview_ledger, policy, dry_run=True)

apply_ledger = Ledger("./private-state/tasks.sqlite")
applied = reconcile_restart(apply_ledger, policy)
print(preview["tasks_timed_out"], applied["tasks_timed_out"])
```

The normal `Ledger` used for apply keeps the existing initialization and safe
schema-migration behavior.

Reconciliation never converts queued work into success, retries a task, or
claims that transport completed.

### Doctor and migration

`doctor` checks SQLite integrity, exact schema and metadata keys, foreign keys,
parent ordering and cycles, canonical identifiers, task/delivery semantics,
finite timestamp order, and each task's complete expected event chain. Findings
make `healthy` false but are never rendered with task IDs or stored values.

Schema v1 migrates transactionally to v2 only when the old schema is exact and
every stored reason already belongs to the fixed registry and matching state. A
v1 database containing arbitrary legacy reason text fails closed; back it up
with SQLite-safe tooling and perform a private, explicit migration.

### Development and release checks

Run source-tree checks from the repository root with a supported interpreter.

macOS or Linux:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/restart_reconciliation_demo.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py --self-test
PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py --sdist /path/to/extracted-sdist
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
py -3 -m unittest discover -s tests -v
py -3 .\examples\restart_reconciliation_demo.py --allow-external-acl
py -3 .\scripts\privacy_audit.py
py -3 .\scripts\privacy_audit.py --self-test
py -3 .\scripts\privacy_audit.py --sdist .\path\to\extracted-sdist
```

For reproducible wheel and sdist builds, artifact inspection, canonicalization,
and the exact cross-platform commands, follow
[RELEASING.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/RELEASING.md).
`artifact_smoke.py` creates an isolated temporary environment, installs the
built wheel offline, and invokes `scripts/install_smoke.py` with that
environment's interpreter; the install-smoke helper is not a standalone check
for an uninstalled checkout.

The synthetic test suite covers deadlines and restart grace, cancellation,
parent/child relationships, delivery reconciliation, concurrent idempotent
closes, schema and event tampering, path and sidecar aliases, POSIX permissions,
the Windows external-ACL contract, CLI redaction, and deterministic doctor
behavior. Dry-run regressions snapshot the directory entry set and the bytes,
mode, and modification time of the main database and existing sidecars. They
also force a writer checkpoint between the two closed-database reads, exercise
the 64 MiB memory boundary, and verify that retained snapshot buffers are wiped
when their in-memory SQLite connection closes.

The publication audit checks release metadata, common secret and personal-data
patterns, generated artifacts, unsafe modes, SPDX headers, Python syntax, and
network/telemetry imports. Reports contain only relative path, category, and
count, never matched source text. `--self-test` uses temporary synthetic canaries
to confirm fail-closed behavior without echoing their values.

`artifact_smoke.py` rejects unsafe archive paths, audits an extracted source
distribution, verifies the wheel's exact member set, canonical `RECORD`, `WHEEL`
and `top_level.txt` semantics, compares runtime source and `LICENSE` byte for
byte with the audited source distribution, installs the wheel offline, and runs
an installed CLI lifecycle.

CI runs tests on Ubuntu with Python 3.11 through 3.14 and on macOS and Windows
with Python 3.11 and 3.14. A separate Python 3.14 matrix builds, audits,
installs, and smoke-tests wheel and source distributions on all three operating
systems. Windows tests validate the explicit external-ACL boundary and portable
state behavior; they do not claim to inspect or establish a DACL.

## Project status

TaskStateGuard is an alpha-stage clean-room implementation licensed under the
Apache License, Version 2.0. See
[LICENSE](https://github.com/MaxHu-xuan/task-state-guard/blob/main/LICENSE),
[PROVENANCE.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/PROVENANCE.md),
[CONTRIBUTING.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/CONTRIBUTING.md),
[SECURITY.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/SECURITY.md),
[SUPPORT.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/SUPPORT.md),
[CHANGELOG.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/CHANGELOG.md),
[CODE_OF_CONDUCT.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/CODE_OF_CONDUCT.md),
[RELEASING.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/RELEASING.md),
[v0.1.0 release checklist](https://github.com/MaxHu-xuan/task-state-guard/blob/main/RELEASE_CHECKLIST_v0.1.0.md),
[v0.1.0 release notes](https://github.com/MaxHu-xuan/task-state-guard/blob/main/RELEASE_NOTES_v0.1.0.md),
and [THREAT_MODEL.md](https://github.com/MaxHu-xuan/task-state-guard/blob/main/THREAT_MODEL.md).
Maintainer checklists and release-process documents describe the required
evidence; their presence alone is not proof that a tag, GitHub Release, or
package publication has completed.
