# Releasing Speechloom

Speechloom publishes the native runtime and Python package separately. Use distinct tag
names so a runtime release cannot trigger the PyPI workflow.

```text
Native runtime:  runtime-v0.1.0
Python package:  v0.1.1
```

Runtime and package versions do not need to match. The Python package contains a pinned
registry entry that tells `speechloom setup` which runtime archive and checksum to use.

## Publish a native runtime

Start with a non-publishing run from `main`:

```bash
gh workflow run runtime-release.yml \
  --ref main \
  -f release_tag=runtime-v0.1.0 \
  -f publish=false
```

This builds, caches, packages, and verifies the CPU and CUDA archives without creating a
Git tag or GitHub Release. Inspect the run before publishing:

```bash
gh run list --workflow runtime-release.yml
gh run watch RUN_ID
```

After the dry run passes, publish the same runtime tag:

```bash
gh workflow run runtime-release.yml \
  --ref main \
  -f release_tag=runtime-v0.1.0 \
  -f publish=true
```

The publishing run should restore the compiled native prefixes from the Actions cache.
It then:

- creates the `runtime-v0.1.0` Git tag and GitHub Release;
- uploads CPU and CUDA archives and checksums;
- opens a pull request containing the pinned runtime registry entries.

If repository policy prevents `GITHUB_TOKEN` from creating pull requests, the workflow
keeps the pushed promotion branch, prints a manual pull-request link in the job summary,
and completes successfully. You can use that link or create the pull request with:

```bash
gh pr create \
  --base main \
  --head runtime-registry-runtime-v0.1.0 \
  --title "Register runtime-v0.1.0 runtime archives" \
  --body "Pins the verified CPU and CUDA runtime archives."
```

To permit automatic creation instead, enable **Settings → Actions → General → Workflow
permissions → Allow GitHub Actions to create and approve pull requests**.

Always dispatch this workflow from `main`, because the generated registry pull request
uses the selected workflow branch as its base. Do not reuse or move a published runtime
tag.

## Prepare the Python package automatically

Run the preparation workflow from `main` with the new package version:

```bash
make prepare-release VERSION=0.1.1
```

By default, it selects the newest stable GitHub release whose tag starts with
`runtime-v`. To select a specific runtime, pass either its version or full tag:

```bash
make prepare-release VERSION=0.1.1 RUNTIME_VERSION=0.1.0
make prepare-release VERSION=0.1.1 RUNTIME_VERSION=runtime-v0.1.0
```

The workflow:

- rejects an existing `vVERSION` package tag;
- resolves and downloads the selected runtime release;
- verifies its published checksums and archive dependencies;
- updates `pyproject.toml`, `src/speechloom/__init__.py`, and the bundled runtime
  registry;
- runs the full test suite;
- creates or reuses `release-vVERSION` and opens a preparation pull request.

The runtime registry promotion pull request may be merged first. If it is still open,
the package preparation pull request can carry the same registry update, and the
standalone promotion pull request can then be closed.

To request automatic merge after required checks pass, opt in explicitly:

```bash
make prepare-release VERSION=0.1.1 AUTO_MERGE=true
```

If GitHub auto-merge or an allowed merge strategy is unavailable, preparation still
succeeds and leaves the pull request open for manual review. This option does not create
the final package tag or publish to PyPI.

You can also dispatch the workflow without Make:

```bash
gh workflow run prepare-release.yml \
  --ref main \
  -f package_version=0.1.1 \
  -f runtime_version=latest \
  -f auto_merge=false
```

## Prepare the Python package manually

Use the version helper to inspect the current version and update `pyproject.toml` and
`src/speechloom/__init__.py` together. For example, prepare version `0.1.1`:

```bash
make version
make set-version VERSION=0.1.1
make check-version

git add pyproject.toml src/speechloom/__init__.py
git commit -m "release: prepare 0.1.1"
git push origin main
```

## Test the Python distribution

Publish to TestPyPI manually:

```bash
gh workflow run release.yml \
  --ref main \
  -f repository=testpypi
```

Install and exercise that release from TestPyPI before publishing to production. PyPI
and TestPyPI do not allow an uploaded version to be replaced, so increment the package
version if another candidate is required.

## Publish to PyPI

Create and push a tag that exactly matches the package version with a leading `v`:

```bash
git tag -a v0.1.1 -m "Speechloom 0.1.1"
git push origin v0.1.1
```

The tag push triggers the production Python release workflow. It runs the release tests,
verifies that the tag matches `pyproject.toml`, builds and validates the wheel and source
distribution, publishes them through PyPI Trusted Publishing, and creates the matching
GitHub Release.

Do not use a plain `v*.*.*` tag for native runtimes: that pattern is reserved for Python
package releases and triggers the PyPI workflow.

## Release order

```text
runtime dry run
  -> native runtime GitHub Release
  -> release preparation pull request (version + selected runtime registry)
  -> review or optional auto-merge
  -> TestPyPI verification
  -> v*.*.* tag
  -> production PyPI release
```
