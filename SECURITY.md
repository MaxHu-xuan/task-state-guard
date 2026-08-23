# Security policy

## Supported versions

This prototype has not published a stable release. Security fixes currently
target the latest revision only.

## Reporting a vulnerability

Do not open a public issue containing a database, payload fingerprint, filesystem
path, task identifier, or reproduction derived from private workloads. Before a
public repository is launched, configure GitHub private vulnerability reporting
and replace this section with its canonical contact path.

A useful private report should include:

- affected version or commit;
- operating system and Python version;
- a minimal synthetic reproduction;
- expected and observed state transitions; and
- whether confidentiality, integrity, or availability is affected.

Never attach a production SQLite file. Reproduce the issue with generated UUIDs
and synthetic hashes instead. Contributions must also follow the sensitive-data
rules in `CONTRIBUTING.md`.

## Operational guidance

- Keep the database on a trusted local filesystem in a directory controlled by
  the service account. On POSIX hosts, the final directory must not be group- or
  world-writable; TaskStateGuard enforces owner/mode checks and sets the database
  and observable sidecars to mode `0600`.
- On Windows, pre-create the complete database directory with a DACL restricted
  to the service account. The library fails closed unless the caller passes
  `allow_external_acl=True` (or CLI `--allow-external-acl`). That opt-in neither
  creates nor verifies the DACL and is not equivalent to a `0600` guarantee.
- Do not use a database, journal, WAL, or SHM symlink, Windows reparse-point,
  or hardlink alias. TaskStateGuard rejects aliases it can observe, but requires
  stable identity and link-count behavior from the local filesystem.
- Back up the database together with its WAL state using SQLite's backup API or
  a filesystem snapshot that is known to be SQLite-safe.
- Use only the documented fixed reason registry. Do not place prompt text, model
  output, secrets, personal data, paths, or exception messages in metadata.
- Treat payload fingerprints as pseudonymous metadata: a low-entropy payload may
  still be vulnerable to guessing.
- Protect successful CLI output. It intentionally contains operational task
  metadata such as UUIDs, parent UUIDs, states, and timestamps even though it
  excludes prompt text and payload fingerprints by default.
- Expected CLI failures emit only a fixed error class, unexpected failures emit
  only `InternalError`, and neither path writes rejected values to the ledger.
  Exit codes are `0` for success, `1` for a completed unhealthy doctor report,
  `2` for bounded input/storage/state errors, and `3` for unexpected errors.
- Run `task-state-guard doctor` after unclean shutdowns and before repair work.
- Treat `healthy=false` and any CLI error as a failure requiring investigation;
  do not infer success from a retained historical task row.
- Expect only the database and SQLite-managed `-journal`, `-wal`, or `-shm`
  sidecars in the state directory; TaskStateGuard creates no application scratch
  or export file and contains no network or telemetry integration.
- Run `python3.12 scripts/privacy_audit.py` and
  `python3.12 scripts/privacy_audit.py --self-test` before creating a release
  artifact.

TaskStateGuard treats the current OS user as trusted. A malicious same-user
process, privileged host actor, incorrectly configured external Windows DACL,
or filesystem with unreliable SQLite locking, identity, or hardlink semantics
remains outside the enforced boundary. UNC paths, mapped network drives, and
remote mounts are unsupported even when the operating system exposes them as a
normal path.
