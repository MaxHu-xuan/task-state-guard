# Release Process

Releases are maintainer-operated. Publishing a Git tag, GitHub release, or
package-index upload is a separate action that requires explicit review.

Before a release:

1. Run the strict test suite and both privacy-audit modes.
2. Build from a clean checkout with the public project timestamp so wheel
   metadata cannot inherit local file times:

   - macOS or Linux: `export SOURCE_DATE_EPOCH=946684800`
   - Windows PowerShell: `$env:SOURCE_DATE_EPOCH = "946684800"`

   Then run `python -m build`.
3. Run `scripts/artifact_smoke.py dist`; verify the extracted sdist audit,
   offline wheel install, module and console entry points, and synthetic task
   lifecycle.
4. Never upload the raw setuptools sdist: its tar headers may retain the build
   account and local file times. Create the upload candidate with the bundled
   canonicalizer, using the documented project epoch:

   ```bash
   python scripts/canonicalize_sdist.py --self-test
   python scripts/canonicalize_sdist.py --dist-dir dist --output-dir canonical-dist --source-date-epoch 946684800
   ```

   The canonicalizer fails closed on links, special files, duplicate or unsafe
   paths, and oversized input. It sets uid/gid to zero, removes user/group and
   optional gzip metadata, fixes member times and modes, and verifies that file
   contents are unchanged. Build twice and compare canonical artifact hashes.
5. Review every distributed file, package metadata, the changelog, security
   policy, threat model, SHA-256 checksums, and SBOM.
6. Confirm the final Linux, macOS, and Windows CI matrix is green.

Do not upload databases, SQLite sidecars, task identifiers, payload
fingerprints, credentials, local paths, logs, build caches, or private test
output. The first public release should be tagged `v0.1.0` only after its
private candidate is approved.
Source publication may proceed without release assets. Only the sdist from
`canonical-dist` may be reviewed for upload; never upload the raw archive from
`dist`.
