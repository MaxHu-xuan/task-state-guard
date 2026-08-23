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

Before submitting a change, run:

```console
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3.12 scripts/privacy_audit.py
PYTHONDONTWRITEBYTECODE=1 python3.12 scripts/privacy_audit.py --self-test
```

CI also runs on Ubuntu with Python 3.11 through 3.14 and on macOS and Windows
with Python 3.11 and 3.14. Platform-specific tests must assert the documented
security boundary. In particular, Windows tests must not treat POSIX mode bits
or `chmod(0o600)` as proof of a private DACL; they must exercise the default
fail-closed behavior and explicit external-ACL acknowledgement.
