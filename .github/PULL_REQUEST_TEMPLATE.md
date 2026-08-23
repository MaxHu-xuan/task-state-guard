## Problem and outcome

Describe the user or operator problem, then the observable result of this change.

## State contract

- Task state affected:
- Delivery state affected:
- Restart, timeout, preview, or doctor behavior affected:
- Compatibility impact:

## Verification

- [ ] Tests pass on the platforms affected by this change.
- [ ] `scripts/privacy_audit.py` passes.
- [ ] `scripts/privacy_audit.py --self-test` passes.
- [ ] Package smoke checks pass when packaging or entry points change.
- [ ] Documentation and `CHANGELOG.md` are updated when the public contract changes.
- [ ] A dry-run or doctor path remains non-mutating when the change affects observability.

## Safety

- [ ] The change contains only synthetic test data and public project metadata.
- [ ] No database, sidecar, task body, identifier, payload fingerprint, private path, credential, or confidential log is included.
- [ ] SQLite identity, POSIX permission, Windows external-ACL, restart, and delivery boundaries are not weakened.
