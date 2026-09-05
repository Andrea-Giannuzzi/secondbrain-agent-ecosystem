# Contributing

Open an issue describing the behavior and a synthetic reproduction. Keep changes
focused and include relevant tests. Never contribute vault content or global
client configurations. The maintainer reviews public patches, applies accepted
changes to local canonical sources and regenerates the distribution.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python scripts/test_all.py
node sbe/bundle/_System/Librarian/Dashboard/test_dashboard_signals.cjs
```

Keep investigation and review read-only with one writer. Distinguish automated
checks from native client and visual evidence. Contributions are submitted under
the repository's Apache-2.0 license.
