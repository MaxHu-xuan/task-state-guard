# Support

TaskStateGuard is an alpha-stage open-source library maintained on a best-effort
basis. No response time, availability, data recovery, or compatibility service
level is promised.

## Choose the right channel

| Your question or problem | Where to go |
|---|---|
| A task, delivery, dry-run preview, restart reconciliation, or `doctor` result does not match the documented state model | Open a GitHub bug report with a minimal synthetic reproduction |
| A new state-ledger outcome would help multiple runtimes | Open a GitHub feature request and describe the workflow problem first |
| You have a focused, tested implementation | Open a pull request that follows [CONTRIBUTING.md](CONTRIBUTING.md) |
| The behavior may expose data, bypass a storage boundary, or weaken fail-closed handling | Use GitHub private vulnerability reporting as described in [SECURITY.md](SECURITY.md) |

Never post a production database, SQLite sidecar, task identifier, payload
fingerprint, filesystem path, credential, task body, prompt, model output, or
confidential log. Use generated UUIDs, temporary local paths, and synthetic
state transitions. Maintainers may close or redact a report that cannot be
handled safely in public.

## What helps us reproduce a problem

Include:

- the TaskStateGuard version or public commit;
- the operating system and CPython version;
- the state before the action;
- the command or API call using synthetic values;
- the expected task, delivery, preview, or doctor result;
- the observed result without private paths or stored values.

For restart or timeout behavior, include the synthetic deadline, heartbeat,
active grace, and delivery grace. For `--dry-run`, say whether time or another
process could have changed the ledger between preview and apply.

## Supported scope

Useful reports cover the current default branch or latest published release on
CPython 3.11 through 3.14, using a supported local filesystem on Linux, macOS,
or Windows.

The public scope includes:

- task and delivery state transitions;
- deadline, heartbeat, and restart reconciliation;
- non-mutating reconciliation previews;
- aggregate `doctor` checks and value-free CLI errors;
- local SQLite integrity and documented path/permission defenses;
- installation and CLI behavior on supported Python and operating-system
  versions.

## Outside the support boundary

TaskStateGuard does not provide operational support for queueing, scheduling,
task execution, automatic retries, process supervision, transport delivery,
recovery of external side effects, distributed consensus, Windows DACL design,
host hardening, disk encryption, SQLite repair, or remote and mapped
filesystems. Those responsibilities remain with the embedding runtime and its
operator.
