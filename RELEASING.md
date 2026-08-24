# Release Process

Releases are maintainer-operated. Publishing a Git tag, GitHub release, or
package-index upload is a separate action that requires explicit review.

For version `0.1.0`, review
[RELEASE_NOTES_v0.1.0.md](RELEASE_NOTES_v0.1.0.md) and record evidence in
[RELEASE_CHECKLIST_v0.1.0.md](RELEASE_CHECKLIST_v0.1.0.md). Those files are
preparation material, not approval.

Before a release:

1. Run the strict test suite, deterministic synthetic demo, and both
   privacy-audit modes:

   - macOS or Linux:

     ```bash
     PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
     PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/restart_reconciliation_demo.py
     PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py
     PYTHONDONTWRITEBYTECODE=1 python3 scripts/privacy_audit.py --self-test
     ```

   - Windows PowerShell:

     ```powershell
     $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
     try {
         $env:PYTHONDONTWRITEBYTECODE = "1"
         $env:PYTHONPATH = "src"
         py -3 -m unittest discover -s tests -v
         py -3 .\examples\restart_reconciliation_demo.py --allow-external-acl
         py -3 .\scripts\privacy_audit.py
         py -3 .\scripts\privacy_audit.py --self-test
     }
     finally {
         if ($null -eq $previousPythonPath) {
             Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
         }
         else {
             $env:PYTHONPATH = $previousPythonPath
         }
     }
     ```

   Confirm that `python3` or `py -3` selects CPython 3.11 through 3.14 before
   treating any result as release evidence. On Windows, confirm the temporary
   directory has the intended private DACL before passing the demo's explicit
   acknowledgement flag. The `finally` block restores the caller's original
   `PYTHONPATH`, or removes the temporary value when none existed, so the build
   step cannot inherit `src` from these source-tree checks.
2. Build from a clean checkout with the pinned CI frontend and public project
   timestamp so wheel metadata cannot inherit local file times:

   - macOS or Linux:

     ```bash
     export SOURCE_DATE_EPOCH=946684800
     python3 -m venv .venv
     . .venv/bin/activate
     python -m pip install "build==1.3.0" "setuptools==77.0.3" "wheel==0.46.2"
     python -m build --no-isolation
     ```

   - Windows PowerShell:

     ```powershell
     $env:SOURCE_DATE_EPOCH = "946684800"
     py -3 -m venv .venv
     .\.venv\Scripts\python.exe -m pip install "build==1.3.0" "setuptools==77.0.3" "wheel==0.46.2"
     .\.venv\Scripts\python.exe -m build --no-isolation
     ```

   Record all three pinned tool versions. `--no-isolation` ensures the build
   uses that reviewed toolchain instead of downloading a second, unrecorded
   backend environment.

3. Never upload the raw setuptools sdist: its tar headers may retain the build
   account and local file times. Create the upload candidate with the bundled
   canonicalizer, using the documented project epoch:

   - macOS or Linux:

     ```bash
     python3 scripts/canonicalize_sdist.py --self-test
     python3 scripts/canonicalize_sdist.py --dist-dir dist --output-dir canonical-dist --source-date-epoch 946684800
     ```

   - Windows PowerShell:

     ```powershell
     py -3 .\scripts\canonicalize_sdist.py --self-test
     py -3 .\scripts\canonicalize_sdist.py --dist-dir .\dist --output-dir .\canonical-dist --source-date-epoch 946684800
     ```

   The canonicalizer fails closed on links, special files, duplicate or unsafe
   paths, and oversized input. It sets uid/gid to zero, removes user/group and
   optional gzip metadata, fixes member times and modes, and verifies that file
   contents are unchanged. Build twice and compare canonical artifact hashes.
4. Stage the wheel together with the canonical sdist, then run the artifact
   smoke against that exact two-file upload set. Do not smoke the raw sdist from
   `dist` as release evidence.

   - macOS or Linux:

     ```bash
     mkdir staging-dist
     cp dist/*.whl staging-dist/
     cp canonical-dist/*.tar.gz staging-dist/
     PYTHONDONTWRITEBYTECODE=1 python3 scripts/artifact_smoke.py staging-dist
     ```

   - Windows PowerShell:

     ```powershell
     New-Item -ItemType Directory -Path .\staging-dist -ErrorAction Stop
     Copy-Item .\dist\*.whl .\staging-dist\ -ErrorAction Stop
     Copy-Item .\canonical-dist\*.tar.gz .\staging-dist\ -ErrorAction Stop
     $env:PYTHONDONTWRITEBYTECODE = "1"
     py -3 .\scripts\artifact_smoke.py .\staging-dist
     ```

   Verify the extracted-sdist audit, offline wheel install, module and console
   entry points, and synthetic task lifecycle. `artifact_smoke.py` creates an
   isolated temporary environment, installs the wheel there, and invokes
   `scripts/install_smoke.py` with that environment's interpreter. Do not run
   the install-smoke helper directly against an uninstalled checkout.
5. Review every distributed file, package metadata, the changelog, security
   policy, threat model, SHA-256 checksums, and SBOM. Build from two separate
   clean candidate directories and require matching wheel and canonical-sdist
   SHA-256 digests. Record the SBOM generator and version, and reject local
   paths or build-account identity in either artifact metadata or SBOM.
6. Confirm the final Linux, macOS, and Windows CI matrix is green.

## Trusted Publishing to PyPI

PyPI publication is tokenless and runs only from
`.github/workflows/publish-pypi.yml` after a non-draft, non-prerelease GitHub
release is published. Configure PyPI's publisher with owner `MaxHu-xuan`,
repository `task-state-guard`, workflow filename `publish-pypi.yml`, and GitHub
environment `pypi`. Configure that environment with a required reviewer and an
exact release-tag deployment rule. If a second trusted reviewer is available,
also prevent self-review; enabling that option with only one reviewer would
make publication impossible.

Prepare the GitHub release as a draft and attach exactly these five externally
reviewed assets before publishing it:

- `SOURCE_COMMIT`
- `SHA256SUMS`
- `task_state_guard-0.1.0.cdx.json`
- `task_state_guard-0.1.0-py3-none-any.whl`
- `task_state_guard-0.1.0.tar.gz`

`SOURCE_COMMIT` contains the exact tagged commit. `SHA256SUMS` contains only the
wheel, canonical sdist, and SBOM digests. Keep digest values in those external
release records rather than copying them into documentation, pull-request
discussion, or workflow source. The verification job rejects any other asset
set, binds tag and package version to `SOURCE_COMMIT`, requires the tag to be in
`main` history, requires the running workflow revision to equal the tagged
commit, reruns the values-free source and canonical-archive gates, and passes
only the wheel and canonical sdist to the protected publishing job.

The `publish` job has only `id-token: write`; it receives no PyPI password or
long-lived API token, checks out no source, builds nothing, and runs no project
code. Never add `PYPI_TOKEN`, a password input, `contents: write`, a reusable
workflow wrapper, or a second package-index invocation to this job. PyPI
Trusted Publishing also emits the supported package attestations.

PyPI uploads of multiple files are not atomic. Keep `skip-existing` disabled so
a duplicate or partial publication is visible. If one distribution uploads and
the other fails, do not blindly rerun and do not claim success. First compare
the public PyPI file hashes with the approved `SHA256SUMS`; only an existing
byte-for-byte match may be retained. Recover a missing file through a separate,
explicitly reviewed action, and treat any mismatch as a release incident. PyPI
does not permit replacing an already published file.

Do not upload databases, SQLite sidecars, task identifiers, payload
fingerprints, credentials, local paths, logs, build caches, or private test
output. The first public release should be tagged `v0.1.0` only after its
private candidate is approved. Approval must identify the exact commit and
artifact digests; any changed input invalidates that approval.
Source publication may proceed without release assets. Only the sdist from
`canonical-dist` may be reviewed for upload; never upload the raw archive from
`dist`.
