import time
import heapq


class TTLCache:
    def __init__(self, default_ttl=None, time_fn=None):
        self._time = time_fn if time_fn is not None else time.monotonic
        self._default_ttl = default_ttl
        self._data = {}
        self._heap = []

    def _expiry_for(self, ttl):
        if ttl is None:
            ttl = self._default_ttl
        if ttl is None:
            return None
        return self._time() + ttl

    def _is_expired(self, expiry):
        return expiry is not None and self._time() >= expiry

    def set(self, key, value, ttl=None):
        expiry = self._expiry_for(ttl)
        self._data[key] = (value, expiry)
        if expiry is not None:
            heapq.heappush(self._heap, (expiry, key))

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
        entry = self._data.get(key)
        if entry is None:
            return False
        _, expiry = entry
        del self._data[key]
        return not self._is_expired(expiry)

    def __len__(self):
        now = self._time()
        return sum(1 for _, e in self._data.values() if e is None or now < e)

    def cleanup(self):
        now = self._time()
        purged = 0
        while self._heap and self._heap[0][0] <= now:
            expiry, key = heapq.heappop(self._heap)
            entry = self._data.get(key)
            if entry is None:
                continue
            _, cur_expiry = entry
            if cur_expiry is None or cur_expiry != expiry:
                continue
            if cur_expiry <= now:
                del self._data[key]
                purged += 1
        return purged
