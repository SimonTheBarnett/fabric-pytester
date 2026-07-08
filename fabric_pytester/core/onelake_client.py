from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from fabric_pytester.core.tokens import ONELAKE_SCOPE, TokenProvider


@dataclass(slots=True)
class OneLakePath:
    file_system: str
    path: str

    @classmethod
    def from_parts(
        cls, workspace: str, lakehouse_root: str, folder: str, filename: str
    ) -> OneLakePath:
        path = str(PurePosixPath(lakehouse_root.strip("/")) / folder.strip("/") / filename)
        return cls(file_system=workspace, path=path)


@dataclass
class UploadedFile:
    path: OneLakePath
    size: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AzureTokenCredential:
    token_provider: TokenProvider

    def get_token(self, *scopes: str, **_: Any) -> Any:
        from azure.core.credentials import AccessToken

        scope = scopes[0] if scopes else ONELAKE_SCOPE
        return AccessToken(
            self.token_provider.get_token(scope),
            int(time.time() + 3300),
        )


@dataclass
class OneLakeClient:
    account_url: str
    workspace: str
    lakehouse_root: str
    token_provider: TokenProvider | None = None
    credential: Any | None = None
    service_client: Any | None = None
    uploads: list[UploadedFile] = field(default_factory=list)

    def _service_client(self) -> Any:
        if self.service_client is None:
            from azure.storage.filedatalake import DataLakeServiceClient

            credential = self.credential
            if credential is None and self.token_provider is not None:
                credential = AzureTokenCredential(self.token_provider)
            if credential is None:
                from azure.identity import DefaultAzureCredential

                credential = DefaultAzureCredential()
            self.service_client = DataLakeServiceClient(
                account_url=self.account_url, credential=credential
            )
        return self.service_client

    def upload(
        self,
        *,
        folder: str,
        filename: str,
        content: bytes | str | dict[str, Any] | list[Any] | None = None,
        records: list[dict[str, Any]] | None = None,
        overwrite: bool = True,
    ) -> UploadedFile:
        data = self._serialize(content=content, records=records)
        target = OneLakePath.from_parts(self.workspace, self.lakehouse_root, folder, filename)
        filesystem = self._service_client().get_file_system_client(target.file_system)
        file_client = filesystem.get_file_client(target.path)
        file_client.upload_data(data, overwrite=overwrite)
        uploaded = UploadedFile(path=target, size=len(data))
        self.uploads.append(uploaded)
        return uploaded

    def download(self, path: OneLakePath) -> bytes:
        filesystem = self._service_client().get_file_system_client(path.file_system)
        downloader = filesystem.get_file_client(path.path).download_file()
        return downloader.readall()

    def delete(self, path: OneLakePath) -> None:
        filesystem = self._service_client().get_file_system_client(path.file_system)
        filesystem.get_file_client(path.path).delete_file()

    def list_paths(self, folder: str, *, recursive: bool = True) -> list[str]:
        root = str(PurePosixPath(self.lakehouse_root.strip("/")) / folder.strip("/"))
        filesystem = self._service_client().get_file_system_client(self.workspace)
        return [item.name for item in filesystem.get_paths(path=root, recursive=recursive)]

    def download_latest(self, folder: str, pattern: str | None = None) -> bytes:
        paths = self.list_paths(folder)
        if pattern:
            import fnmatch

            paths = [path for path in paths if fnmatch.fnmatch(PurePosixPath(path).name, pattern)]
        if not paths:
            raise FileNotFoundError(f"No OneLake files found in {folder!r}")
        return self.download(OneLakePath(self.workspace, sorted(paths)[-1]))

    def delete_tracked_uploads(self) -> None:
        for uploaded in reversed(self.uploads):
            self.delete(uploaded.path)
        self.uploads.clear()

    @staticmethod
    def _serialize(
        *,
        content: bytes | str | dict[str, Any] | list[Any] | None,
        records: list[dict[str, Any]] | None,
    ) -> bytes:
        value: Any = records if records is not None else content
        if value is None:
            value = b""
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        return json.dumps(value).encode("utf-8")
