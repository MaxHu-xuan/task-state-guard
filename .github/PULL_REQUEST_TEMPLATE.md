## Summary

Describe the public behavior or invariant changed by this pull request.

## Verification

- [ ] Tests pass on the platforms affected by this change.
- [ ] `scripts/privacy_audit.py` passes.
- [ ] `scripts/privacy_audit.py --self-test` passes.
- [ ] Package smoke checks pass when packaging or entry points change.
- [ ] Documentation and `CHANGELOG.md` are updated when the public contract changes.

## Safety

- [ ] The change contains only synthetic test data and public project metadata.
- [ ] No database, sidecar, task body, identifier, payload fingerprint, private path, credential, or confidential log is included.
- [ ] SQLite identity, POSIX permission, Windows external-ACL, restart, and delivery boundaries are not weakened.
