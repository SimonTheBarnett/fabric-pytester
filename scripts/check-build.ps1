$ErrorActionPreference = "Stop"

uv sync --dev
uv run pytest
uv run ruff format .
uv run ruff check .
uv run ty check

Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
uv build