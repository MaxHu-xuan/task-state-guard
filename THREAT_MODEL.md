# Threat model

This document describes security boundaries, not additional license terms. The
project is licensed under the Apache License, Version 2.0; see `LICENSE`.

## Assets

TaskStateGuard protects the integrity of task and delivery state while minimizing
the confidential information stored alongside that state. The database contains
UUIDs, timestamps, bounded machine reason codes, parent relationships, and
optional SHA-256 payload fingerprints.

## Trust boundary

The host process and local service account are trusted. SQLite files, WAL files,
backups, shell history, and command output may be inspected by an operator. No
remote service is trusted or required because the package contains no network
client or server.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| A crash leaves work apparently active forever | Restart reconciliation closes stale running work after an explicit grace period. |
| A retry overwrites a real terminal outcome | Terminal and delivery closes are idempotent but conflicting closes are rejected. |
| Two workers close the same task concurrently | On a supported local filesystem, `BEGIN IMMEDIATE`, WAL, `busy_timeout`, and conditional state checks serialize writers. |
| Internal child work is reported as undelivered | `delivery_required=false` reconciles to `not_applicable`. |
| A prompt leaks through persistence or diagnostics | There is no plaintext payload field; only a validated SHA-256 fingerprint is accepted, and doctor output is counts-only. |
| Corrupt or spoofed storage is mistaken for healthy state | The exact closed schema-metadata key/value set, normalized DDL fingerprint, `quick_check`, foreign keys, state/timestamp invariants, and exact event chains all participate in doctor health. |
| Arbitrary error text becomes a covert data channel | Reason codes come from a fixed registry, are mapped to compatible states, and rejected CLI values are never echoed. |
| Rejected sensitive input reaches SQLite or process streams | Validation precedes mutations; bounded errors disclose only a fixed class, unexpected errors disclose only `InternalError`, and synthetic tests scan stdout, stderr, the database, WAL, and SHM for canaries. |
| An unhealthy doctor report is treated as command success | A completed `healthy=false` report exits with status `1`; healthy reports exit `0`. |
| Scratch files retain task content | Runtime code creates no application-managed temporary or export files; only the database and SQLite-managed sidecars are expected in the state directory. |
| A hidden network or telemetry path exports metadata | Runtime dependencies are empty, runtime code has no network client, and the release audit rejects direct Python network or telemetry imports. |
| Parent references point outside the ledger | Foreign-key enforcement is enabled on every connection. |
| A path alias redirects writes to another file | Observable main-database and SQLite-sidecar symlinks, Windows reparse points, and multi-hardlinks are rejected; stable file identity is required and checked around connection opening. |
| Another OS account swaps POSIX path entries during open | The final parent must be owned by the service account and not group/world writable; writable ancestors must use sticky-directory semantics. |
| A Windows database inherits a permissive DACL | Windows use fails closed by default. The caller must pre-create a private directory and explicitly acknowledge external ACL enforcement; the opt-in does not verify the DACL. |
| A prior schema stored arbitrary reason metadata | v1 migration validates reason values and state compatibility and fails closed before upgrading unsafe metadata. |

## Residual risks and non-goals

- SHA-256 does not hide low-entropy inputs from offline guessing. Use a keyed
  fingerprint outside this package if that threat matters.
- Successful CLI output contains operational identifiers, relationships,
  timestamps, and states. It must be protected by the invoking process; the
  value-free failure contract does not make successful output anonymous.
- A privileged host attacker can read or alter the database and application
  memory. Disk encryption and OS hardening are outside this package.
- Processes running as the same OS user are trusted. They can race path operations
  or alter a SQLite file directly; schema fingerprints detect accidents and many
  tampering cases but are not an authenticity signature against a local writer.
- The Python standard library cannot validate or install a Windows DACL
  equivalent to POSIX `0600`. Windows operation therefore depends on an
  externally configured private directory and explicit caller acknowledgement.
  CI validates this fail-closed boundary, not DACL confidentiality.
- UNC paths, mapped network drives, remote mounts, and filesystems without
  reliable identity, SQLite locking, or hardlink behavior are unsupported. The
  library cannot reliably detect every mapped or mounted remote filesystem.
- The ledger does not execute, retry, schedule, cancel, or deliver work. It only
  records and reconciles state supplied by a trusted runtime.
- Reconciliation cannot determine whether an external side effect occurred. A
  transport must record its own acknowledgement before marking `delivered`.
- SQLite replication, distributed consensus, and multi-host leader election are
  out of scope.
- `doctor` diagnoses aggregate consistency; it neither repairs data nor proves
  that an external delivery side effect occurred.
- The publication audit detects a bounded set of artifact, content, metadata,
  syntax, and direct-import hazards. It does not prove absence of obfuscated
  runtime loading, malicious standard-library composition, or legal conflicts;
  source and artifact review remain required.
