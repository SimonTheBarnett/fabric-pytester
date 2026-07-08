import sys
from types import SimpleNamespace

import pytest

from fabric_pytester.adapters.jdbc_token import create_fabric_jdbc_token_backend
from fabric_pytester.adapters.mssql_python import create_mssql_python_backend
from fabric_pytester.adapters.pyodbc import create_pyodbc_backend
from fabric_pytester.core.errors import ConfigError
from fabric_pytester.core.sql_backend import DbApiSqlBackend, LazySqlBackend, SqlBackendInfo
from fabric_pytester.core.tokens import StaticTokenProvider
from fabric_pytester.fixtures.plugin import _prepare_sql_config, _run_sql_diagnostics


class Cursor:
    description = [("Name",)]

    def __init__(self):
        self.closed = False
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self):
        return [("value",)]

    def close(self):
        self.closed = True


class Connection:
    def __init__(self):
        self.cursors = []
        self.committed = False
        self.closed = False
        self.autocommit = False

    def cursor(self):
        cursor = Cursor()
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class JdbcConnection(Connection):
    def __init__(self):
        super().__init__()
        self.jconn = SimpleNamespace(calls=[])

        def set_auto_commit(value):
            self.jconn.calls.append(value)

        self.jconn.setAutoCommit = set_auto_commit


def test_dbapi_backend_closes_cursors_and_connection():
    connection = Connection()
    backend = DbApiSqlBackend(connection)
    backend.execute("insert")
    rows = backend.fetch_all("select")
    backend.close()
    assert connection.committed
    assert connection.autocommit is True
    assert rows == [{"Name": "value"}]
    assert all(cursor.closed for cursor in connection.cursors)
    assert connection.closed


def test_dbapi_backend_enables_jdbc_autocommit_when_available():
    connection = JdbcConnection()

    DbApiSqlBackend(connection)

    assert connection.jconn.calls == [True]


def test_dbapi_backend_applies_query_timeout_to_cursors():
    connection = Connection()
    backend = DbApiSqlBackend(connection, query_timeout_seconds=12)
    backend.execute("insert")
    backend.fetch_all("select")

    assert [cursor.timeout for cursor in connection.cursors] == [12, 12]


def test_lazy_sql_backend_close_releases_backend_and_recreates():
    created = []

    def factory():
        connection = Connection()
        created.append(connection)
        return DbApiSqlBackend(connection)

    backend = LazySqlBackend(factory)
    backend.fetch_all("select")
    backend.close()
    backend.fetch_all("select again")
    assert len(created) == 2
    assert created[0].closed


def test_fabric_jdbc_token_backend_uses_token_provider(monkeypatch):
    calls = {}

    def connect(driver_class, jdbc_url, properties, driver_path):
        calls["driver_class"] = driver_class
        calls["jdbc_url"] = jdbc_url
        calls["properties"] = properties
        calls["driver_path"] = driver_path
        return Connection()

    monkeypatch.setitem(sys.modules, "jaydebeapi", SimpleNamespace(connect=connect))

    create_fabric_jdbc_token_backend(
        {
            "sql_hostname": "example.datawarehouse.fabric.microsoft.com",
            "sql_database": "warehouse",
            "driver_path": "mssql-jdbc.jar",
        },
        {},
        StaticTokenProvider("fabric-token"),
    )

    assert calls["driver_class"] == "com.microsoft.sqlserver.jdbc.SQLServerDriver"
    assert "database=warehouse" in calls["jdbc_url"]
    assert calls["properties"]["accessToken"] == "fabric-token"
    assert calls["driver_path"] == "mssql-jdbc.jar"


def test_mssql_python_backend_uses_connection_and_query_timeouts(monkeypatch):
    calls = {}

    def connect(connection_string):
        calls["connection_string"] = connection_string
        return Connection()

    monkeypatch.setitem(sys.modules, "mssql_python", SimpleNamespace(connect=connect))

    backend = create_mssql_python_backend(
        {
            "sql_hostname": "example.datawarehouse.fabric.microsoft.com",
            "sql_database": "warehouse",
            "sql_connection_timeout_seconds": 7,
            "sql_query_timeout_seconds": 11,
        },
        {"FABRIC_CLIENT_ID": "client", "FABRIC_CLIENT_SECRET": "secret"},
    )

    backend.fetch_all("select 1")

    assert isinstance(backend, DbApiSqlBackend)
    assert "Connection Timeout=7;" in calls["connection_string"]
    assert backend.connection.cursors[0].timeout == 11


def test_pyodbc_backend_can_use_connection_string_and_user_password(monkeypatch):
    calls = {}

    def connect(connection_string):
        calls["connection_string"] = connection_string
        return Connection()

    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect))

    create_pyodbc_backend(
        {
            "connection_string": (
                "Driver={ODBC Driver 18 for SQL Server};"
                "Server=tcp:host,1433;"
                "Database=db;"
                "Encrypt=yes;"
            ),
            "user_secret": "REPORTING_DB_USER",
            "password_secret": "REPORTING_DB_PASSWORD",
            "sql_connection_timeout_seconds": 9,
        },
        {"REPORTING_DB_USER": "user", "REPORTING_DB_PASSWORD": "password"},
    )

    assert "UID=user;" in calls["connection_string"]
    assert "PWD=password;" in calls["connection_string"]
    assert "Connection Timeout=9;" in calls["connection_string"]


def test_prepare_sql_config_resolves_jdbc_driver_path(tmp_path, capsys):
    driver = tmp_path / "drivers" / "vendor.jar"
    driver.parent.mkdir()
    driver.write_text("jar", encoding="utf-8")

    config = _prepare_sql_config(
        {"sql_adapter": "jdbc", "driver_path": "drivers/vendor.jar"},
        tmp_path,
        debug=True,
    )

    assert config["driver_path"] == str(driver.resolve())
    assert f"JDBC driver_path={driver.resolve()} exists=True" in capsys.readouterr().out


def test_prepare_sql_config_fails_when_jdbc_driver_path_is_missing(tmp_path):
    with pytest.raises(ConfigError, match="Configured JDBC driver file does not exist"):
        _prepare_sql_config(
            {"sql_adapter": "jdbc", "driver_path": "drivers/missing.jar"},
            tmp_path,
            debug=False,
        )


def test_sql_diagnostics_runs_db_name_query(capsys):
    connection = Connection()
    backend = DbApiSqlBackend(
        connection,
        info=SqlBackendInfo(adapter="mssql-python", host="host", database="configured_db"),
    )

    _run_sql_diagnostics(backend, enabled=True)

    assert connection.cursors[0].executed == [("SELECT DB_NAME()", ())]
    assert "SQL diagnostics adapter=mssql-python database=value" in capsys.readouterr().out
