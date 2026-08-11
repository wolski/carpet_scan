# carpet_scan

Carpet diagnostics: aggregates pyan3, vulture, radon and grimp into module-level quality tables and a Markdown report.

## Development

```bash
uv sync --group dev
make check
.venv/bin/pre-commit install --hook-type pre-commit --hook-type pre-push
```

All Python commands run from the synchronized project `.venv`.
