import time


class TTLCache:
    def __init__(self, default_ttl=None, time_fn=None):
        self._default_ttl = default_ttl
        self._time = time_fn if time_fn is not None else time.monotonic
        self._data = {}

    def _expiry(self, ttl):
        if ttl is None:
            ttl = self._default_ttl
        if ttl is None:
            return None
        return self._time() + ttl

    def _is_expired(self, expiry):
        return expiry is not None and self._time() >= expiry

    def set(self, key, value, ttl=None):
        self._data[key] = (value, self._expiry(ttl))

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if self._is_expired(expiry):
            del self._data[key]
            return default
        return value

    def delete(self, key):
        entry = self._data.pop(key, None)
        if entry is None:
            return False
        return not self._is_expired(entry[1])

    def __len__(self):
        return sum(1 for _, e in self._data.values() if not self._is_expired(e))

    def cleanup(self):
        expired = [k for k, (_, e) in self._data.items() if self._is_expired(e)]
        for k in expired:
            del self._data[k]
        return len(expired)
