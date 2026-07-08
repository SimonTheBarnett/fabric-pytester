from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from fabric_pytester.core.sql_backend import DbApiSqlBackend, SqlBackend, SqlBackendInfo
from fabric_pytester.core.tokens import FABRIC_SCOPE, TokenProvider


def create_fabric_jdbc_token_backend(
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    token_provider: TokenProvider | None = None,
) -> SqlBackend:
    if token_provider is None:
        raise ValueError("fabric-jdbc-token requires a token provider")

    jaydebeapi = import_module("jaydebeapi")
    driver_class = config.get("driver_class", "com.microsoft.sqlserver.jdbc.SQLServerDriver")
    jdbc_url = config.get("jdbc_url") or _fabric_jdbc_url(config)
    driver_path = config.get("driver_path")
    properties = {
        "accessToken": token_provider.get_token(str(config.get("scope", FABRIC_SCOPE))),
        "encrypt": "true",
        "trustServerCertificate": "false",
    }
    connection = jaydebeapi.connect(driver_class, jdbc_url, properties, driver_path)
    return DbApiSqlBackend(
        connection,
        info=SqlBackendInfo(
            adapter="fabric-jdbc-token",
            host=str(config.get("sql_hostname")) if config.get("sql_hostname") else None,
            database=str(config.get("sql_database")) if config.get("sql_database") else None,
        ),
    )


def _fabric_jdbc_url(config: Mapping[str, Any]) -> str:
    host = config["sql_hostname"]
    database = config["sql_database"]
    return (
        f"jdbc:sqlserver://{host}:1433;"
        f"database={database};"
        "encrypt=true;"
        "trustServerCertificate=false;"
        "loginTimeout=30;"
    )
