# TaskStateGuard v0.1.0

## 中文

发布日期：2026-08-25

### 这个版本解决的问题

服务重启后，任务可能仍显示为 `running`，投递状态也可能长时间保持 `pending`。这些状态
不等于工作还在继续，更不能作为成功或已经送达的证据。TaskStateGuard 是一个嵌入式
SQLite 状态对账护栏，把任务是否结束与结果是否送达分开记录，让重启后的状态保持真实、
可预览、可审计。

使用者可以先运行只返回聚合计数的预览，确认会影响多少记录，再显式应用相同的对账规则；
随后由 `doctor` 检查 SQLite 完整性、数据库结构、状态、时间戳、父子关系、外键和事件链。
这些诊断不会列出任务 ID、任务正文或投递内容。

### v0.1.0 包含

- 不可改写的任务终态和投递终态；重复写入相同终态是幂等的，冲突终态会被拒绝。
- 以明确宽限期处理重启遗留状态：超过截止时间或失去心跳的 `running` 任务转为
  `timed_out`，仍然新鲜的任务保持不变。
- 任务终态与投递终态独立：已进入终态且需要外部投递的任务在宽限期后仍无回执时，
  投递状态转为 `failed`；已进入终态且无需直接投递的内部任务转为 `not_applicable`。
- `reconcile --dry-run` 只读预览、显式执行和幂等复查使用同一套判断规则，并返回
  不含任务 ID 的聚合计数。
- `doctor` 对当前账本执行只读健康检查；真实的结构、状态、时间戳或事件链异常会令健康
  结果失败，已正确闭合的历史记录不会仅因保留而触发失败。
- SQLite WAL 事务、受控数据库结构迁移，以及针对正在使用的 WAL 和稳定内存快照的安全
  只读预览。
- Linux、macOS 的 POSIX 所有者与权限保护，以及 Windows 上明确由调用方管理的私有
  DACL 边界。
- 完全合成、结果可复现的演示，覆盖 preview、apply、幂等复查和 `doctor`，不会读取
  本机任务数据。
- Linux、macOS、Windows 跨平台测试，隐私审计，离线安装验证，严格制品检查和可复现的
  规范化 sdist。

### 最重要的语义边界

TaskStateGuard 不会把未知结果猜成成功。`reconcile` 只会把符合超时条件的活跃任务收敛为
`timed_out`；只有外部 worker 真实确认工作完成后，调用方才能写入 `succeeded`。

任务完成也不代表用户已经收到结果。只有传输系统记录了真实回执后，调用方才能把投递
状态写为 `delivered`。没有回执且超过投递宽限期时，外部投递状态会明确变为 `failed`，
不会伪装成已送达。

TaskStateGuard 不是队列、调度器、worker、重试服务、工作流引擎、进程监管器或传输层。
它不执行或续跑任务，也不保证恰好一次执行或恰好一次投递。真实的外部副作用只能由周边
worker 或传输系统确认。

数据库结构没有提示词、消息正文、任务正文、文件路径或自由文本异常字段。可选的
SHA-256 payload 指纹仍是可关联的假名化元数据；不需要时可以省略。

### 安装与支持环境

对于已经发布到 PyPI 的版本，建议在独立虚拟环境中安装。

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

支持 CPython 3.11 至 3.14，以及使用本地文件系统、SQLite 兼容锁和稳定文件身份的 Linux、
macOS 与 Windows。运行时代码仅使用 Python 标准库，不包含网络请求或遥测。

Windows 使用者必须先为数据库父目录配置私有 DACL，再显式确认这一外部安全边界。
TaskStateGuard 不会创建、检查或认证 DACL。所有平台均不支持网络盘、映射盘和远程挂载。

### 发布完整性

发布流程对 GitHub Release 的恰好五项上传资产执行门禁，绑定 `SOURCE_COMMIT`、
`SHA256SUMS`、CycloneDX SBOM、审核后的 wheel 和规范化 sdist。GitHub 自动提供的源码
下载不计入上传资产；原始 setuptools sdist 不属于可发布制品。

每次发布前，维护者必须确认 GitHub 已配置受保护的 `pypi` 发布环境，并确认 PyPI Trusted
Publishing 已注册匹配的仓库、工作流和环境。发布任务通过 GitHub OIDC 获得短期身份，
不保存长期包索引令牌；它不构建代码、不检出源码，只接收已经通过检查的 wheel 和规范化
sdist。如出现部分上传，必须先将 PyPI 上已有文件的摘要与审核记录逐项核对，再单独审批
恢复操作。

---

## English

Release date: 2026-08-25

### What this release solves

After a restart, a task may still appear `running` and its delivery status may
remain `pending`. Neither state proves that work is still running, that it
succeeded, or that its result reached the intended recipient. TaskStateGuard is
an embedded SQLite reconciliation guardrail that records task completion
separately from result delivery, keeping post-restart state truthful, reviewable,
and auditable.

Users can preview aggregate changes before explicitly applying the same
reconciliation policy. A counts-only `doctor` then checks SQLite integrity, the
schema, states, timestamps, parent relationships, foreign keys, and event
chains without listing task IDs, task bodies, or delivery content.

### Included in v0.1.0

- Immutable task and delivery terminal states. Repeating the same terminal write
  is idempotent; a conflicting terminal write is rejected.
- Explicit restart grace rules: a `running` task past its deadline or heartbeat
  grace becomes `timed_out`, while fresh work remains unchanged.
- Separate task and delivery outcomes. For a delivery-required terminal task
  without an acknowledgement, the delivery status becomes `failed` after grace;
  a terminal internal task with no direct delivery obligation becomes
  `not_applicable`.
- A read-only `reconcile --dry-run`, explicit apply, and idempotent recheck that
  share one decision policy and return aggregate counts without task IDs.
- A counts-only `doctor` that fails health on real schema, state, timestamp, or
  event-chain inconsistencies without failing merely because consistent terminal
  history is retained.
- SQLite WAL transactions, guarded schema migration, and safe read-only preview
  behavior for live WAL or bounded stable in-memory snapshots.
- POSIX owner and mode protection on Linux and macOS, plus an explicit
  caller-managed private-DACL boundary on Windows.
- A deterministic, fully synthetic demo covering preview, apply, an idempotent
  recheck, and `doctor` without reading local task data.
- Cross-platform Linux, macOS, and Windows tests, privacy auditing, offline
  installation smoke tests, strict artifact inspection, and reproducible
  canonical sdist tooling.

### The most important semantic boundaries

TaskStateGuard never guesses that unknown work succeeded. `reconcile` can close
eligible active work only as `timed_out`; callers may write `succeeded` only
after the real worker has confirmed completion.

Task completion also does not prove delivery. Callers may write `delivered` only
after the transport records a real acknowledgement. If a delivery-required
result remains unacknowledged past its grace period, its delivery status becomes
explicitly `failed`; it is never silently marked as delivered.

TaskStateGuard is not a queue, scheduler, worker, retry service, workflow
engine, process supervisor, or transport. It does not execute or resume work
and does not guarantee exactly-once execution or exactly-once delivery. Only the
surrounding worker or transport can confirm real external side effects.

The schema has no field for prompts, messages, task bodies, filesystem paths, or
free-form exception text. The optional SHA-256 payload fingerprint remains
correlatable pseudonymous metadata and can be omitted.

### Installation and supported environments

After a release is published to PyPI, install it in an isolated virtual
environment.

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

TaskStateGuard supports CPython 3.11 through 3.14 on Linux, macOS, and Windows
using a local filesystem with SQLite-compatible locking and stable file
identity. Runtime code uses only the Python standard library and has no network
or telemetry path.

Windows callers must configure a private DACL on the database parent directory
and explicitly acknowledge this external boundary before use. TaskStateGuard
does not create, inspect, or certify the DACL. Network drives, mapped drives,
and remote mounts are unsupported on every platform.

### Release integrity

The release process applies an exact five-uploaded-asset GitHub Release gate,
binding `SOURCE_COMMIT`, `SHA256SUMS`, the CycloneDX SBOM, the reviewed wheel,
and the canonical sdist. GitHub's automatic source downloads do not count as
uploaded assets. The raw setuptools sdist is not a publishable artifact.

Before each release, maintainers must ensure that a protected GitHub `pypi`
environment is configured and that the matching repository, workflow, and
environment are registered in PyPI Trusted Publishing. The publishing job
obtains short-lived identity through GitHub OIDC without a long-lived
package-index token. It does not build or check out source and receives only the
verified wheel and canonical sdist. If an upload is partial, maintainers must
compare every existing PyPI file digest with the reviewed record before
separately approving recovery.
