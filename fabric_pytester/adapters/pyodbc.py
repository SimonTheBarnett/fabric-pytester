from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from fabric_pytester.core.sql_backend import DbApiSqlBackend, SqlBackend, SqlBackendInfo
from fabric_pytester.core.tokens import TokenProvider


def create_pyodbc_backend(
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    token_provider: TokenProvider | None = None,
) -> SqlBackend:
    pyodbc = import_module("pyodbc")

    connection_string = config.get("connection_string")
    driver = config.get("sql_driver", "ODBC Driver 18 for SQL Server")
    host = config.get("sql_hostname")
    database = config.get("sql_database")
    connection_timeout = config.get("sql_connection_timeout_seconds")
    query_timeout = config.get("sql_query_timeout_seconds")
    user = secrets.get(str(config.get("user_secret", ""))) or config.get("user")
    password = secrets.get(str(config.get("password_secret", ""))) or config.get("password")
    client_id = (
        secrets.get(str(config.get("client_id_secret", "")))
        or secrets.get("FABRIC_CLIENT_ID")
        or config.get("client_id")
    )
    client_secret = (
        secrets.get(str(config.get("client_secret_secret", "")))
        or secrets.get("FABRIC_CLIENT_SECRET")
        or config.get("client_secret")
    )
    if connection_string:
        connection_string = str(connection_string)
        if user and "UID=" not in connection_string.upper():
            connection_string += f"UID={user};"
        if password and "PWD=" not in connection_string.upper():
            connection_string += f"PWD={password};"
    else:
        host = config["sql_hostname"]
        database = config["sql_database"]
        connection_string = (
            f"DRIVER={{{driver}}};"
            f"SERVER=tcp:{host},1433;"
            f"DATABASE={database};"
            "Authentication=ActiveDirectoryServicePrincipal;"
            f"UID={client_id};"
            f"PWD={client_secret};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
        )
    if connection_timeout is not None:
        connection_string += f"Connection Timeout={int(connection_timeout)};"
    return DbApiSqlBackend(
        pyodbc.connect(connection_string),
        query_timeout_seconds=int(query_timeout) if query_timeout is not None else None,
        info=SqlBackendInfo(
            adapter="pyodbc",
            host=str(host) if host else None,
            database=str(database) if database else None,
        ),
    )
