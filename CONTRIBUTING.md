# Contributing

Thank you for contributing to TaskStateGuard.

By intentionally submitting a contribution for inclusion in this project, you
agree that it is provided under the Apache License, Version 2.0, without
additional terms or conditions, as described in Section 5 of `LICENSE`.

Use only synthetic data in issues, tests, examples, and patches. Never submit
credentials, secrets, personal data, private task content, production database
files, filesystem paths from private systems, or other confidential material.
Keep diagnostics counts-only and reproduce problems with generated UUIDs and
synthetic hashes.

## Development setup

Use CPython 3.11 through 3.14 and confirm that the selected interpreter is in
that range. A virtual environment is recommended.

macOS or Linux:

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

On Windows, use `.\.venv\Scripts\task-state-guard.exe` for the console entry
point. Do not weaken the fail-closed ACL behavior to make a test convenient:
create a synthetic parent directory, apply the test host's external ACL policy,
and pass the explicit acknowledgement.

Before submitting a change, run:

macOS or Linux:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/restart_reconciliation_demo.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py --self-test
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\examples\restart_reconciliation_demo.py --allow-external-acl
.\.venv\Scripts\python.exe .\scripts\privacy_audit.py
.\.venv\Scripts\python.exe .\scripts\privacy_audit.py --self-test
```

For packaging changes, also build both distributions and run the artifact smoke:

macOS or Linux:

```bash
export SOURCE_DATE_EPOCH=946684800
python3 -m pip install "build==1.3.0"
python3 -m build
PYTHONDONTWRITEBYTECODE=1 python3 scripts/artifact_smoke.py dist
```

Windows PowerShell:

```powershell
$env:SOURCE_DATE_EPOCH = "946684800"
.\.venv\Scripts\python.exe -m pip install "build==1.3.0"
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe .\scripts\artifact_smoke.py .\dist
```

The artifact smoke creates an isolated temporary environment, installs the
built wheel offline, and invokes `scripts/install_smoke.py` with that
environment's interpreter. Do not run `scripts/install_smoke.py` directly
against an uninstalled checkout. Release candidates must also follow the
canonicalization and two-build comparison in [RELEASING.md](RELEASING.md).

CI also runs on Ubuntu with Python 3.11 through 3.14 and on macOS and Windows
with Python 3.11 and 3.14. Platform-specific tests must assert the documented
security boundary. In particular, Windows tests must not treat POSIX mode bits
or `chmod(0o600)` as proof of a private DACL; they must exercise the default
fail-closed behavior and explicit external-ACL acknowledgement.

## Pull requests

- Keep a pull request focused and explain the state or security invariant it
  preserves.
- Add synthetic regression coverage for behavior changes, including error paths.
- Update `README.md`, `CHANGELOG.md`, and the threat model when a public contract
  changes.
- Confirm that tests, privacy audit, audit self-test, and relevant package smoke
  checks pass.
- Do not commit generated databases, SQLite sidecars, archives, wheels, caches,
  virtual environments, or package metadata directories.
