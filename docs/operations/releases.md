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

## Promote the runtime registry

Review and merge the registry pull request created by the runtime workflow. A Python
package published before this merge will not know about the new runtime archive and may
fall back to a local source build.

## Prepare the Python package

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
  -> registry promotion pull request
  -> registry merge
  -> package version update
  -> TestPyPI verification
  -> v*.*.* tag
  -> production PyPI release
```
