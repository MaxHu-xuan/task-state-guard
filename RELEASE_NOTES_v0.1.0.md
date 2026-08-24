# TaskStateGuard v0.1.0 Release Notes

Release date: 2026-08-25.

## Why TaskStateGuard

After an agent or worker process restarts, stale `running` tasks and uncertain
`pending` delivery can leave operators with a misleading view of the workflow.
TaskStateGuard is an embedded SQLite state-reconciliation guardrail. It keeps
task terminal state separate from delivery terminal state, supports a
counts-only preview before applying reconciliation, and provides a counts-only
consistency doctor.

## Included in v0.1.0

- Immutable task and delivery terminal states with idempotent matching writes.
- Preview-first reconciliation for stale running tasks and overdue terminal
  delivery, using explicit grace periods and aggregate output without task IDs.
- A counts-only doctor for SQLite integrity, schema, state, timestamp,
  parent-child, foreign-key, and event-chain consistency.
- Local SQLite WAL transactions, guarded schema migration, and safe read-only
  preview behavior for live WAL or bounded stable in-memory snapshots.
- POSIX owner and mode protection on Linux and macOS, plus an explicit
  caller-managed private-DACL boundary on Windows.
- A deterministic, fully synthetic demo that runs preview, apply, an idempotent
  recheck, and `doctor` without reading local task data.
- Cross-platform tests, values-free privacy auditing, offline install smoke
  tests, strict artifact inspection, and reproducible canonical sdist tooling.
- A five-file GitHub Release gate that binds the source commit, checksums,
  CycloneDX SBOM, wheel, and canonical sdist before tokenless PyPI Trusted
  Publishing can enter its protected environment.

## Supported environments

- CPython 3.11 through 3.14.
- Linux, macOS, and Windows on a local filesystem with SQLite-compatible locks
  and stable file identity.
- Python standard-library-only runtime with no network or telemetry path.

Windows callers must create the database parent directory with a private DACL
before use and explicitly acknowledge that external boundary. TaskStateGuard
does not create, inspect, or certify the DACL. Network drives, mapped drives,
and remote mounts are unsupported on every platform.

## Important boundaries

TaskStateGuard is not a queue, scheduler, worker, retry service, workflow
engine, process supervisor, or transport. It does not execute or resume work,
and it does not guarantee exactly-once execution or exactly-once delivery. Only
the surrounding worker or transport can confirm real external side effects.

The schema has no field for prompts, messages, task bodies, filesystem paths,
or free-form exception text. The optional payload SHA-256 fingerprint remains
correlatable pseudonymous metadata and can be omitted.

## Release integrity

Use [RELEASE_CHECKLIST_v0.1.0.md](RELEASE_CHECKLIST_v0.1.0.md) for the exact
candidate gate. The GitHub Release contains the external `SOURCE_COMMIT` and
`SHA256SUMS` records alongside the SBOM, reviewed wheel, and canonical sdist;
digest values are intentionally not duplicated in this document. The raw
setuptools sdist must not be uploaded.

PyPI publication uses GitHub OIDC and the protected `pypi` environment. The
publishing job has no long-lived package-index token, does not build or check
out source, and receives only the verified wheel and canonical sdist. A partial
two-file upload remains a visible failure: existing PyPI hashes must match the
external checksum record before any separately approved recovery uploads the
missing file.
