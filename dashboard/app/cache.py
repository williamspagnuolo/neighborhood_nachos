from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """Simple in-memory TTL cache for callback query results."""

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 512) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() >= expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self.ttl_seconds
        self._data[key] = (expires_at, value)
        self._data.move_to_end(key)
        self._evict_expired()
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def _evict_expired(self) -> None:
        now = time.time()
        expired_keys = [key for key, (expires_at, _) in self._data.items() if expires_at <= now]
        for key in expired_keys:
            self._data.pop(key, None)
