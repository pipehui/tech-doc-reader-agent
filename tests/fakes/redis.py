from __future__ import annotations

from threading import Lock

from redis.exceptions import ConnectionError


class FakeRedisBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.lock = Lock()


class FakeRedisClient:
    def __init__(self, backend: FakeRedisBackend | None = None) -> None:
        self.backend = backend or FakeRedisBackend()
        self.closed = False

    def set(self, key, value, ex):
        with self.backend.lock:
            self.backend.values[key] = value
            self.backend.expirations[key] = ex
        return True

    def get(self, key):
        with self.backend.lock:
            return self.backend.values.get(key)

    def getdel(self, key):
        with self.backend.lock:
            self.backend.expirations.pop(key, None)
            return self.backend.values.pop(key, None)

    def close(self) -> None:
        self.closed = True


class FailingRedisClient(FakeRedisClient):
    def get(self, key):
        raise ConnectionError("redis://admin:private-password@internal-host")


__all__ = [
    "FailingRedisClient",
    "FakeRedisBackend",
    "FakeRedisClient",
]
