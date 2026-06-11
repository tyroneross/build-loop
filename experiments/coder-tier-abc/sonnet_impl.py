import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, default_ttl=None, time_fn=None):
        self._default_ttl = default_ttl
        self._time_fn = time_fn if time_fn is not None else time.monotonic
        self._store = {}

    def set(self, key, value, ttl=None):
        resolved_ttl = ttl if ttl is not None else self._default_ttl
        if resolved_ttl is None:
            expires_at = None
        else:
            expires_at = self._time_fn() + resolved_ttl
        self._store[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._store.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if expires_at is not None and self._time_fn() >= expires_at:
            del self._store[key]
            return default
        return value

    def delete(self, key) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        value, expires_at = entry
        del self._store[key]
        if expires_at is not None and self._time_fn() >= expires_at:
            return False
        return True

    def __len__(self):
        now = self._time_fn()
        return sum(1 for _, (_, e) in self._store.items() if e is None or e > now)

    def cleanup(self) -> int:
        now = self._time_fn()
        ek = [k for k, (_, e) in self._store.items() if e is not None and now >= e]
        for k in ek:
            del self._store[k]
        return len(ek)
