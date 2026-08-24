# Changelog

All notable changes to TaskStateGuard are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - 2026-08-25

### Added

- Independent task and delivery state machines with immutable terminal states.
- Atomic SQLite transitions, WAL operation, schema fingerprinting, and a
  counts-only consistency doctor.
- Restart reconciliation for stale running tasks and overdue pending delivery.
- Read-only restart-reconciliation previews through the Python API and
  `reconcile --dry-run`, with explicit `dry_run` and `applied` report fields.
- Full-pipeline read-only ledger opening for CLI previews, including safe
  non-migrating schema-v1 inspection, stable bounded in-memory snapshots for
  closed databases, normal SQLite locking for live WAL state, and
  snapshot-before-clock concurrency.
- Parent/child task relationships and explicit internal-task delivery semantics.
- Fixed reason-code registries and value-free CLI error output.
- Linux and macOS owner/mode enforcement plus explicit externally managed
  Windows ACL acknowledgement.
- Main-database and SQLite-sidecar alias defenses for symlinks, Windows reparse
  points, non-regular files, and multiple hardlinks.
- Cross-platform tests, privacy auditing, and isolated package-install smoke
  checks.
- A cross-platform canonical source-archive builder that strips build-account
  ownership metadata, rejects unsafe members, and verifies unchanged content.
- A deterministic, fully synthetic preview/apply/idempotent/doctor demo that
  creates and removes its SQLite ledger at runtime.
- A release-asset gate and tokenless PyPI Trusted Publishing workflow that
  binds the public tag, source commit, checksums, SBOM, wheel, and canonical
  sdist before an environment-approved upload.

### Fixed

- Keep source-archive identity checks fail-closed on Windows without comparing
  path and open-handle timestamps that have different operating-system meanings.
- Keep queued tasks unchanged during restart reconciliation even when their
  precomputed deadline has passed; only running tasks are eligible for
  deadline-based timeout transitions.
- Parse wheel core metadata as structured RFC-style headers so valid Windows
  CRLF output is accepted while missing, duplicated, or body-only fields still
  fail the artifact gate.
- Require an exact wheel member set, a canonical closed-set SHA-256 `RECORD`,
  and byte-for-byte agreement between wheel runtime sources, its license, and
  the audited source distribution.
- Validate the installer-facing `WHEEL` fields and `top_level.txt` so a rebuilt
  valid `RECORD` cannot conceal package-semantic changes.
- Fix release timestamps to a public project epoch so wheel metadata does not
  retain local checkout times.
- Smoke-test the canonical source distribution that is eligible for upload,
  rather than treating the raw setuptools archive as release evidence.

### Documentation

- Add answer-first English and Chinese guidance for agent restart recovery,
  SQLite task reconciliation, delivery acknowledgement, platform boundaries,
  and common integration questions.
- Add a three-project chooser, platform-specific demo commands, dated v0.1.0
  release notes, and an explicit candidate checklist.
- Document the five-file GitHub Release allowlist, external checksum records,
  least-privilege OIDC publication, and fail-visible partial-upload recovery.
