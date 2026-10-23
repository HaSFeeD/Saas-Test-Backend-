from __future__ import annotations

from pathlib import Path


class Storage:
    async def put(self, key: str, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def get(self, key: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    async def delete(self, key: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class LocalStorage(Storage):
    """Local-disk storage. Swap for an S3 implementation of the same interface in production."""

    def __init__(self, directory: str) -> None:
        self._root = Path(directory).resolve()

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not str(path).startswith(str(self._root) + "/"):
            raise ValueError("invalid storage key")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    async def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)
