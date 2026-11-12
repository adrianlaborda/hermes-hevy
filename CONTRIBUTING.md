# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome. For anything larger than a small fix, open
an issue first.

## Development

The tests need no network, no Hevy account and no Hermes checkout:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
python -m pytest -q
hermes plugins validate .
```

CI runs the tests on Python 3.11 and 3.13 and `hermes plugins validate --install-deps` against the latest stable
Hermes Agent release. Run `--install-deps` locally only in a throwaway Hermes install: it installs into Hermes'
own environment.

## Conventions

- Use only Hermes' public plugin extension points. Never patch Hermes core.
- The plugin talks to no host other than `api.hevyapp.com`.
- Every write to Hevy goes through the approval hook (`approval.py`). A new write tool must be added to the write
  tool set and its preview must say what will change.
- The API key never reaches the model, the chat, logs or the cache. Tests and fixtures use synthetic values only:
  never commit a real key, real workouts, real body measurements or a real account export.
- Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for every user-visible change.

## Releases

Maintainers release by:
1. Moving the `Unreleased` entries under a new version heading.
2. Bumping the version in `plugin.yaml`, `pyproject.toml` and `__version__` in `hermes_hevy/__init__.py`.
3. Pushing a tag `vX.Y.Z`.

By contributing you agree that your work is licensed under the [MIT License](LICENSE).
