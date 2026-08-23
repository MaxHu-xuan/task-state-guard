# Provenance

TaskStateGuard is a clean-room implementation of a behavior-level specification:
record task and delivery metadata transactionally, reconcile stale states after
restart, and diagnose consistency without persisting task bodies.

The implementation in this directory was written from that functional problem
statement. It was not copied or mechanically translated from a production
runtime, production Git history, private repository, generated runtime bundle,
or another project's source files. No production database, chat record, user
identity, credential, article corpus, attachment, or private test fixture was
used. Tests generate temporary UUIDs, hashes, files, and SQLite databases at
runtime and remove them afterward.

Runtime code uses only the Python standard library and does not make network or
telemetry calls. Repository hosting, package publication, and release signing do
not alter this source-level provenance statement.

Machine-verifiable release checks are kept inside this clean-room directory. The
synthetic suite exercises transactional state invariants, restart and delivery
reconciliation, path and file-mode defenses, deterministic CLI status, and
canary absence from stdout, stderr, SQLite, WAL, and SHM. The values-free
publication audit verifies required release metadata, the Apache-2.0 license
digest, common sensitive-content signatures, generated and persistent artifacts,
unsafe modes, SPDX headers, Python syntax, and direct network/telemetry imports.
Its `--self-test` creates only temporary synthetic canaries and confirms they are
detected without being reproduced in audit output. CI also builds wheel and
source distributions on Linux, macOS, and Windows, audits each extracted source
distribution, and installs each wheel offline in a fresh environment before
running the public CLI lifecycle.

The project is licensed under the Apache License, Version 2.0; see `LICENSE`.
Before any public release, a human maintainer must:

1. confirm copyright ownership and contributor authorization;
2. review dependency, name, trademark, export, and platform-policy obligations;
3. run the complete synthetic tests, `scripts/privacy_audit.py`, and
   `scripts/privacy_audit.py --self-test` from the exact release tree;
4. inspect the built wheel and source distribution; and
5. verify that no private files, generated databases, build caches, credentials,
   user data, or unrelated repository history are included.

The automated checks are deliberately bounded and do not prove authorship,
non-infringement, trademark clearance, or absence of deliberately obfuscated
behavior. This note records development provenance and is not a legal opinion.
