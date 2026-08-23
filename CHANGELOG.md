# Changelog

All notable changes to TaskStateGuard are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

### Added

- Independent task and delivery state machines with immutable terminal states.
- Atomic SQLite transitions, WAL operation, schema fingerprinting, and a
  counts-only consistency doctor.
- Restart reconciliation for stale running tasks and overdue pending delivery.
- Parent/child task relationships and explicit internal-task delivery semantics.
- Fixed reason-code registries and value-free CLI error output.
- Linux and macOS owner/mode enforcement plus explicit externally managed
  Windows ACL acknowledgement.
- Main-database and SQLite-sidecar alias defenses for symlinks, Windows reparse
  points, non-regular files, and multiple hardlinks.
- Cross-platform tests, privacy auditing, and isolated package-install smoke
  checks.

### Fixed

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

### Documentation

- Add answer-first English and Chinese guidance for agent restart recovery,
  SQLite task reconciliation, delivery acknowledgement, platform boundaries,
  and common integration questions.
