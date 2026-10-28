"""Controlled local artifact storage.

The adapter accepts only server-generated storage keys.  It never opens a
client path and publishes a file only after an atomic replace.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class ArtifactStorageError(RuntimeError):
    pass


class StorageNotConfigured(ArtifactStorageError):
    pass


class StorageContentConflict(ArtifactStorageError):
    pass


class StorageContentInvalid(ArtifactStorageError):
    pass


@dataclass(frozen=True)
class ScanTarget:
    """A verified server-owned file identity passed to a scanner."""

    storage_key: str
    path: Path
    identity: tuple[int, int]
    size_bytes: int
    sha256: str


class ArtifactStore(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def max_size_bytes(self) -> int: ...

    def key_for(self, artifact_id: str) -> str: ...

    def put(
        self,
        artifact_id: str,
        content: bytes | Iterable[bytes],
        *,
        expected_size: int,
        expected_sha256: str | None = None,
    ) -> tuple[str, int, str]: ...

    def read(self, storage_key: str) -> bytes: ...

    def remove(self, storage_key: str) -> None: ...


class LocalArtifactStore:
    """A server-controlled filesystem store with atomic publication."""

    def __init__(self, root: str | os.PathLike[str] | None, *, max_size_bytes: int = 100 * 1024 * 1024) -> None:
        self._root = Path(root).expanduser().resolve() if root else None
        self._max_size_bytes = max_size_bytes
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)

    @property
    def configured(self) -> bool:
        return self._root is not None and self._root.is_dir()

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    def _require_root(self) -> Path:
        if not self.configured:
            raise StorageNotConfigured("制品存储未配置")
        assert self._root is not None
        return self._root

    @staticmethod
    def _validate_key(storage_key: str) -> None:
        path = Path(storage_key)
        if not storage_key or "\x00" in storage_key or path.is_absolute() or path.drive:
            raise StorageContentInvalid("storage key 无效")
        if any(part in {"", ".", ".."} for part in storage_key.replace("\\", "/").split("/")):
            raise StorageContentInvalid("storage key 不允许路径遍历")

    def _path_for(self, storage_key: str) -> Path:
        root = self._require_root()
        self._validate_key(storage_key)
        target = (root / Path(storage_key)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise StorageContentInvalid("storage key 超出制品根目录") from exc
        return target

    def _reject_reparse_components(self, storage_key: str) -> Path:
        root = self._require_root()
        self._validate_key(storage_key)
        path = root
        for part in storage_key.replace("\\", "/").split("/"):
            path = path / part
            try:
                info = path.lstat()
            except FileNotFoundError as exc:
                raise StorageContentInvalid("制品内容不存在") from exc
            attributes = getattr(info, "st_file_attributes", 0)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(info.st_mode) or attributes & reparse:
                raise StorageContentInvalid("制品路径包含重解析点")
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StorageContentInvalid("storage key 超出制品根目录") from exc
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise StorageContentInvalid("制品内容不是普通文件")
        return resolved

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        info = path.stat()
        # A zero file id cannot bind a scan result to a stable filesystem object.
        if not info.st_ino:
            raise StorageContentInvalid("无法确认制品文件身份")
        return int(info.st_dev), int(info.st_ino)

    @staticmethod
    def _digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def prepare_scan_target(self, storage_key: str, *, expected_sha256: str, expected_size: int) -> ScanTarget:
        """Resolve and verify a stored object before handing its path to a scanner."""

        path = self._reject_reparse_components(storage_key)
        identity = self._file_identity(path)
        size, digest = self._digest(path)
        if (size != expected_size or digest != expected_sha256
                or self._file_identity(self._reject_reparse_components(storage_key)) != identity):
            raise StorageContentInvalid("制品内容与持久化摘要不一致")
        return ScanTarget(storage_key, path, identity, size, digest)

    def assert_scan_target_unchanged(self, target: ScanTarget) -> None:
        """Fail closed if the path, file identity, size, or content changed."""

        path = self._reject_reparse_components(target.storage_key)
        if path != target.path or self._file_identity(path) != target.identity:
            raise StorageContentInvalid("制品文件身份在校验期间发生变化")
        size, digest = self._digest(path)
        if size != target.size_bytes or digest != target.sha256:
            raise StorageContentInvalid("制品内容在校验期间发生变化")

    def key_for(self, artifact_id: str) -> str:
        if not artifact_id or "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            raise StorageContentInvalid("artifact id 无效")
        return f"objects/{artifact_id}/content"

    def put(
        self,
        artifact_id: str,
        content: bytes | Iterable[bytes],
        *,
        expected_size: int,
        expected_sha256: str | None = None,
    ) -> tuple[str, int, str]:
        if expected_size <= 0 or expected_size > self.max_size_bytes:
            raise StorageContentInvalid("文件大小超出限制")
        storage_key = self.key_for(artifact_id)
        target = self._path_for(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            existing_size = target.stat().st_size
            incoming_hasher = hashlib.sha256()
            incoming_size = 0
            chunks = [content] if isinstance(content, (bytes, bytearray, memoryview)) else content
            for chunk in chunks:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise StorageContentInvalid("上传内容必须是二进制")
                incoming_size += len(chunk)
                if incoming_size > self.max_size_bytes or incoming_size > expected_size:
                    raise StorageContentInvalid("文件大小超出声明值或限制")
                incoming_hasher.update(chunk)
            incoming_digest = incoming_hasher.hexdigest()
            if incoming_size == expected_size and incoming_digest == existing_digest and existing_size == expected_size and (expected_sha256 is None or incoming_digest == expected_sha256):
                return storage_key, existing_size, existing_digest
            raise StorageContentConflict("上传内容与已接收内容不一致")

        hasher = hashlib.sha256()
        received = 0
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".upload-", delete=False) as handle:
                temp_name = handle.name
                chunks = [content] if isinstance(content, (bytes, bytearray, memoryview)) else content
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise StorageContentInvalid("上传内容必须是二进制")
                    received += len(chunk)
                    if received > self.max_size_bytes or received > expected_size:
                        raise StorageContentInvalid("文件大小超出声明值或限制")
                    handle.write(chunk)
                    hasher.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            digest = hasher.hexdigest()
            if received != expected_size:
                raise StorageContentInvalid("文件大小与声明不一致")
            if expected_sha256 is not None and digest != expected_sha256:
                raise StorageContentInvalid("文件 SHA-256 与声明不一致")
            # The filesystem rename cannot participate in the DB transaction.
            # If the caller later rolls back, this server-generated key is an
            # orphan; retrying the same digest is safe and assembly must run a
            # TTL sweeper for unreferenced objects.
            os.replace(temp_name, target)
            temp_name = None
            return storage_key, received, digest
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def read(self, storage_key: str) -> bytes:
        path = self._path_for(storage_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageContentInvalid("制品内容不存在") from exc

    def remove(self, storage_key: str) -> None:
        path = self._path_for(storage_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return


__all__ = [
    "ArtifactStore",
    "ArtifactStorageError",
    "LocalArtifactStore",
    "StorageContentConflict",
    "StorageContentInvalid",
    "StorageNotConfigured",
    "ScanTarget",
]
