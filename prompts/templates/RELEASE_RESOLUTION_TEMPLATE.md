# Windows release preparation

Prepare `Sloppy Log Explorer v{VERSION}` from the intended SloppyLogExplorer
checkout. Replace the placeholders with the requested version, representative
log fixture and release notes path. Publication requires an explicit request.

## Local preparation

1. Read `AGENTS.md`, the build instructions in `README.md`, and the release
   entry in `CHANGELOG.md`. On `release/v{VERSION}`, run
   `python tools/session_preflight.py --mode release --version {VERSION}`.
   Commit the intended release inputs. Use a clean, isolated checkout for the
   build so ignored machine-local files cannot enter the package.
2. Record the full commit and tree **before building**. Keep these values
   with the validation results. Use the recorded commit throughout.

   ```powershell
   $buildCommit = git rev-parse HEAD
   $buildTree = git rev-parse "$buildCommit^{tree}"
   git status --porcelain --untracked-files=all
   ```

   Require an empty status. Run `python -m pytest` and build the minimal
   onefile using `.\build_windows.bat`. Stop if either command fails.
3. Record the executable SHA-256 **before testing**. Run the packaged smoke
   and log checks using disposable app settings. This PowerShell example uses
   Python to wait for each Windows GUI executable and propagate its exit code.

   ```powershell
   $exe = (Resolve-Path '.\dist\SloppyLogExplorer.exe').Path
   $testedExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
   @'
   import os
   import subprocess
   import sys
   import tempfile

   with tempfile.TemporaryDirectory() as state:
       env = dict(os.environ, APPDATA=state, LOCALAPPDATA=state, TEMP=state, TMP=state)
       subprocess.run([sys.argv[1], "--smoke-test"], env=env, check=True)
       subprocess.run([sys.argv[1], "--validate-log", sys.argv[2]], env=env, check=True)
   '@ | python - "$exe" "{REPRESENTATIVE_LOG}"
   if ($LASTEXITCODE -ne 0) { throw 'Packaged validation failed' }
   ```

   Stop on a failed check. Run any additional packaged checks called for by
   the release. If source inputs or the executable change, repeat from step 2.
4. Generate notes from the changelog and create the public onefile ZIP.

   ```powershell
   python tools/write_release_notes.py --version {VERSION} --output "{RELEASE_NOTES_FILE}"
   if ($LASTEXITCODE -ne 0) { throw 'Release notes generation failed' }
   python tools/package_release.py --version {VERSION} --source-commit $buildCommit --tested-exe-sha256 $testedExeHash
   if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
   ```

   Store release notes under the ignored `validation_artifacts/` directory.
   The ZIP defaults to `dist/`. An explicit `--output` can point to an
   untracked, Git-ignored `.zip` in the checkout or a `.zip` in a staging
   directory outside Git repositories. The packager compares source
   trees and reads the completed ZIP to check its exact contents, CRCs and
   embedded executable SHA-256 before replacing the output.
5. Record the path, size, and SHA-256 of the resulting archive.

   ```powershell
   $archive = Get-Item 'dist/SloppyLogExplorer-{VERSION}-windows-x64.zip'
   $zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName).Hash
   ```

   Report the build commit/tree, tested EXE hash, archive size/hash, notes
   and observed check results. A local preparation request ends here.

## Authorized publication

1. Push, open a PR, or merge only as requested. After integration, resolve the
   **exact commit to tag** on the intended remote's merged `main` and record
   its tree. If it differs from `$buildTree`, repeat the clean build, packaged
   checks, notes and ZIP preparation on that exact source. If the trees match,
   the tested artifact can be reused. Record both commit IDs and the shared tree.
2. When tagging and publication are authorized, verify the intended remote,
   tag availability and version, and recheck the ZIP's SHA-256 against the
   recorded value. Tag the specific verified commit. Push that tag and publish
   only the verified ZIP, using the prepared notes. With GitHub CLI use
   `gh release create v{VERSION} <verified-zip> --verify-tag --title "Sloppy Log Explorer v{VERSION}" --notes-file "{RELEASE_NOTES_FILE}"`.
3. Read back the release metadata and actual uploaded asset from the intended
   repository. Download the named ZIP into a fresh directory and compare its
   SHA-256 to the recorded local ZIP hash.

   ```powershell
   gh release view v{VERSION} --json tagName,name,url,isDraft,isPrerelease,assets
   $download = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ([guid]::NewGuid().ToString()))
   gh release download v{VERSION} --pattern $archive.Name --dir $download.FullName
   if ($LASTEXITCODE -ne 0) { throw 'Release asset download failed' }
   $assetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $download.FullName $archive.Name)).Hash
   if ($assetHash -ne $zipHash) { throw 'Uploaded archive SHA-256 differs' }
   ```

   Report the tag commit, release URL, asset name, and readback result.
