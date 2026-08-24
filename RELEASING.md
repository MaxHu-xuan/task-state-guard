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

3. Run the artifact smoke with the same supported interpreter:

   - macOS or Linux:

     ```bash
     PYTHONDONTWRITEBYTECODE=1 python3 scripts/artifact_smoke.py dist
     ```

   - Windows PowerShell:

     ```powershell
     $env:PYTHONDONTWRITEBYTECODE = "1"
     py -3 .\scripts\artifact_smoke.py .\dist
     ```

   Verify the extracted-sdist audit, offline wheel install, module and console
   entry points, and synthetic task lifecycle. `artifact_smoke.py` creates an
   isolated temporary environment, installs the wheel there, and invokes
   `scripts/install_smoke.py` with that environment's interpreter. Do not run
   the install-smoke helper directly against an uninstalled checkout.
4. Never upload the raw setuptools sdist: its tar headers may retain the build
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
5. Review every distributed file, package metadata, the changelog, security
   policy, threat model, SHA-256 checksums, and SBOM. Build from two separate
   clean candidate directories and require matching wheel and canonical-sdist
   SHA-256 digests. Record the SBOM generator and version, and reject local
   paths or build-account identity in either artifact metadata or SBOM.
6. Confirm the final Linux, macOS, and Windows CI matrix is green.

Do not upload databases, SQLite sidecars, task identifiers, payload
fingerprints, credentials, local paths, logs, build caches, or private test
output. The first public release should be tagged `v0.1.0` only after its
private candidate is approved. Approval must identify the exact commit and
artifact digests; any changed input invalidates that approval.
Source publication may proceed without release assets. Only the sdist from
`canonical-dist` may be reviewed for upload; never upload the raw archive from
`dist`.
