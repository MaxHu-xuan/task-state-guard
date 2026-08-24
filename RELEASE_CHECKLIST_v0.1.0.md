# TaskStateGuard v0.1.0 Release Checklist

This checklist prepares a candidate; it does not authorize a tag, GitHub
release, or package-index upload. Leave every item unchecked until the evidence
comes from the exact candidate tree.

## Candidate identity and review

- [ ] Record the reviewed commit and verify the candidate worktree is clean.
- [ ] Confirm `pyproject.toml`, `CHANGELOG.md`, and
  `RELEASE_NOTES_v0.1.0.md` all describe version `0.1.0` consistently.
- [ ] Review every tracked file plus every reachable Git ref and object for
  private data, databases, build output, credentials, local paths, task values,
  and unrelated history.
- [ ] Confirm contributor authorization, Apache-2.0 licensing, project-name and
  trademark review, and any applicable export or platform obligations.

## Behavior and privacy evidence

- [ ] Run the strict unit-suite command for the target platform from
  `RELEASING.md`, with `PYTHONDONTWRITEBYTECODE=1`.
- [ ] Run the platform-specific synthetic-demo command from `RELEASING.md`
  twice and confirm identical preview, apply, idempotent-recheck, and healthy
  counts-only doctor output.
- [ ] Run both platform-specific privacy-audit commands from `RELEASING.md`.
- [ ] Confirm the repository contains no generated database, SQLite sidecar,
  wheel, sdist, cache, local log, or test output.
- [ ] Confirm the documented boundaries still say no task execution, retry,
  interrupted-computation resumption, or exactly-once guarantee.

## Reproducible artifacts

- [ ] Set `SOURCE_DATE_EPOCH=946684800` for every candidate build.
- [ ] Build wheel and raw sdist twice from separate clean candidate directories.
- [ ] Run the platform-specific canonicalizer self-test from `RELEASING.md`.
- [ ] Canonicalize both raw sdists, compare their SHA-256 digests, and confirm
  byte-for-byte reproducibility.
- [ ] Stage each wheel with its matching canonical sdist, run the
  platform-specific artifact-smoke command from `RELEASING.md`, and review its
  values-free result, including its isolated offline install smoke.
- [ ] Confirm all wheel member timestamps use the public project epoch and both
  wheel builds have the same SHA-256 digest.
- [ ] Inspect canonical tar ownership, modes, timestamps, PAX headers, gzip
  headers and trailer, archive paths, member types, and extracted content.
- [ ] Generate `SHA256SUMS` from the final wheel and canonical sdist in an
  isolated candidate directory, then independently verify every entry.
- [ ] Generate and review an SBOM with the selected tool name and version
  recorded; confirm it contains no local paths or build-account identity.

## Platform and publication gate

- [ ] Confirm the final Linux, macOS, and Windows CI test and package matrices
  pass on the exact candidate commit.
- [ ] On Windows, confirm the documentation requires an externally managed
  private DACL and does not claim the library verifies it.
- [ ] Obtain explicit human approval for the exact wheel, canonical sdist,
  checksums, SBOM, release notes, and target repositories.
- [ ] Confirm the GitHub Release contains exactly `SOURCE_COMMIT`, `SHA256SUMS`,
  the reviewed SBOM, wheel, and canonical sdist, and that the protected `pypi`
  environment has its required reviewer and exact release-tag rule.
- [ ] Only after approval, create tag `v0.1.0`, publish the GitHub release, and
  perform any separately approved package-index upload.

If any evidence changes, invalidate the approval and repeat the affected steps.
Never upload the raw setuptools sdist from `dist`.
