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
