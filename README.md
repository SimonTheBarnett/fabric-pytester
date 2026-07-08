# fabric-pytester

`fabric-pytester` is a pytest plugin and scenario runner for Microsoft Fabric integration tests.

The package is Fabric-first at its center. It provides Fabric job execution, Fabric SQL assertions, OneLake file setup/assertions, variable rendering, retries, cleanup, and a small destination interface for connecting the systems your project depends on.

You can extend the runner with destinations for Dataverse, SQL Server, REST APIs, internal services, or any other setup/assertion surface. Each destination is a small Python object registered under a scenario name, so scenario JSON stays concise while environment-specific logic stays in code.

## Install

```bash
pip install fabric-pytester
```

## Docs

- [README_QUICKSTART.md](https://github.com/SimonTheBarnett/fabric-pytester/blob/main/README_QUICKSTART.md): start here for a Fabric plus OneLake scenario.
- [README_USAGE_GUIDE.md](https://github.com/SimonTheBarnett/fabric-pytester/blob/main/README_USAGE_GUIDE.md): runner concepts, step naming, and framework extension patterns.
- [README_INTEGRATIONS_AND_CI.md](https://github.com/SimonTheBarnett/fabric-pytester/blob/main/README_INTEGRATIONS_AND_CI.md): Dataverse, SQL, REST, and CI examples.
- [examples](https://github.com/SimonTheBarnett/fabric-pytester/tree/main/examples): copyable destination snippets and a small example project layout.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff format .
uv run ruff check .
uv run ty check
uv build
```

The package version is controlled in `pyproject.toml`. `fabric_pytester.__version__` is read from installed package metadata.
