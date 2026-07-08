# Example Project

This directory shows a small consuming project that uses `fabric-pytester` as a pytest plugin.

It includes:

- `pyproject.toml`: a minimal project and pytest setup.
- `fabric-pytester.toml`: Fabric, OneLake, and named SQL backend configuration.
- `fabric-pytester.complete.example.toml`: a fuller standalone config with dev/QA environments, secrets, Fabric SQL, JDBC, and pyodbc examples.
- `conftest.py`: plugin activation and a project-owned Dataverse destination fixture.
- `tests/destinations/dataverse.py`: a copyable Dataverse destination implementation.
- `tests/fabric/scenarios/`: example scenario JSON files. Add more files here as long as top-level scenario keys stay unique.
- `tests/fabric/test_orders_pipeline.py`: a class-method scenario group.
- `tests/fabric/test_returns_pipeline.py`: a function-based scenario group using a separate scenario file.

The connection strings, workspace ids, and URLs are examples. Replace them with project values, then run from this directory:

```bash
pytest --fabric-env=dev
```

The test files show both supported shapes: `TestOrdersPipeline.test_orders_pipeline` is a marked class method, while `test_returns_pipeline` is a marked module-level function. Each scenario key in the marked test's list is collected and reported as its own pytest test.

For each marked function or method, all scenario inserts run first, then the configured Fabric pipeline runs are completed before scenario assertions begin. Later scenario pytest items for the same marked test reuse that prepared state and run their own assertions. Another marked test gets its own separate setup and pipeline cycle.

The `orders_jdbc_source_to_fabric` scenario demonstrates using a named JDBC SQL backend for setup while keeping Fabric SQL assertions on the default Fabric destination.

`fabric-pytester.toml` can live alongside `pyproject.toml`. Use `pyproject.toml` for Python package metadata and pytest settings; use `fabric-pytester.toml` for Fabric test environments, secrets, OneLake, and SQL backend configuration.
