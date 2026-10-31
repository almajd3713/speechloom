# Contributing

This guide covers the development setup, the checks every change must pass, and how
releases work.

## Development setup

```bash
git clone https://github.com/almajd3713/speechloom.git
cd speechloom
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[api,test]" "ruff==0.17.3" "mypy==2.4.1"
```

For a native engine and models inside the checkout, follow
[docs/operations/repository-local-setup.md](docs/operations/repository-local-setup.md).
With an editable install, `speechloom setup` imports that checkout's `.runtime/` assets
in place. Set `SPEECHLOOM_REPOSITORY_ROOT` to use another checkout's scripts.

## Checks

Run these from the repository root before opening a pull request. CI runs the same:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests scripts
.venv/bin/mypy
```

The web interface in `src/speechloom/web/` is plain ES modules with no build step and no
`package.json`. Its checks need Node 24 or newer, and CI runs them in the `web` job:

```bash
find src/speechloom/web -name '*.js' -print0 | xargs -0 -n1 node --check
node --test 'tests/web/*.test.mjs'
```

Keep logic that needs no DOM (parsing, progress maths, edit rules) in modules that Node can
import. Never build markup from strings: text goes in through `h()` and text nodes.

The real-runtime tests in `tests/integration/` run only when `SPEECHLOOM_TEST_NEMO`,
`SPEECHLOOM_TEST_MODEL` and `SPEECHLOOM_TEST_MEDIA` point at an engine, a model and a
media file.

## Compatibility rules

- `tests/fixtures/cli/*.txt` freeze the CLI help. A change to an option or its help text
  must update the matching snapshot with
  `COLUMNS=80 .venv/bin/python -m speechloom <command> --help > tests/fixtures/cli/<command>.txt`
  (`root.txt` holds the top-level help) and be called out in the changelog.
- Manifest, transcript and installation-state files are versioned. Changes must stay
  readable by version-1 readers or ship a migration with tests.
- External tools run only through argument lists, never through a shell.
- Python 3.10 must keep working.

## Commits and pull requests

Commit messages follow Conventional Commits (`fix: ...`, `feat: ...`, `docs: ...`). Add
a line to the `Unreleased` section of `CHANGELOG.md` for every user-visible change.

## Releases

Maintainers release through GitHub Actions, as described in
[docs/operations/releases.md](docs/operations/releases.md).
