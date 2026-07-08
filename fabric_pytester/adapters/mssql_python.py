from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fabric_pytester.core.sql_backend import DbApiSqlBackend, SqlBackend, SqlBackendInfo
from fabric_pytester.core.tokens import TokenProvider


def create_mssql_python_backend(
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    token_provider: TokenProvider | None = None,
) -> SqlBackend:
    from mssql_python import connect

    host = config["sql_hostname"]
    database = config["sql_database"]
    connection_timeout = config.get("sql_connection_timeout_seconds")
    query_timeout = config.get("sql_query_timeout_seconds")
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
    connection_string = (
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
        connect(connection_string),
        query_timeout_seconds=int(query_timeout) if query_timeout is not None else None,
        info=SqlBackendInfo(
            adapter=str(config.get("sql_adapter") or "mssql-python"),
            host=str(host),
            database=str(database),
        ),
    )
