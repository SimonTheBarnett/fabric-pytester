from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from fabric_pytester.core.sql_backend import DbApiSqlBackend, SqlBackend, SqlBackendInfo
from fabric_pytester.core.tokens import TokenProvider


def create_jdbc_backend(
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    token_provider: TokenProvider | None = None,
) -> SqlBackend:
    jaydebeapi = import_module("jaydebeapi")

    user = secrets.get(str(config.get("user_secret", ""))) or config.get("user")
    password = secrets.get(str(config.get("password_secret", ""))) or config.get("password")
    connection = jaydebeapi.connect(
        config["driver_class"],
        config["jdbc_url"],
        [user, password],
        config.get("driver_path"),
    )
    return DbApiSqlBackend(
        connection,
        info=SqlBackendInfo(
            adapter="jdbc",
            host=str(config.get("sql_hostname")) if config.get("sql_hostname") else None,
            database=str(config.get("sql_database")) if config.get("sql_database") else None,
        ),
    )
