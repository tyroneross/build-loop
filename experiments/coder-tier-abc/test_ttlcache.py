import importlib, sys
import pytest

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def adv(self, dt): self.t += dt

IMPLS = ["sonnet_impl", "opus_impl", "fable_impl"]

def load(name):
    return importlib.import_module(name).TTLCache

@pytest.mark.parametrize("impl", IMPLS)
class TestTTL:
    def test_basic(self, impl):
        c = load(impl)(); c.set("a", 1); assert c.get("a") == 1
    def test_absent(self, impl):
        c = load(impl)(); assert c.get("x") is None; assert c.get("x", -1) == -1
    def test_expiry(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=10)
        ck.adv(5); assert c.get("a") == 1
        ck.adv(6); assert c.get("a") is None
    def test_boundary_expired_at_exact(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=10)
        ck.adv(10); assert c.get("a") is None  # now >= expiry => expired
    def test_overwrite_resets_expiry(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=10)
        ck.adv(8); c.set("a", 2, ttl=10); ck.adv(5)  # now=13, new expiry=18
        assert c.get("a") == 2
    def test_ttl_zero_and_negative(self, impl):
        c = load(impl)(); c.set("a", 1, ttl=0); assert c.get("a") is None
        c.set("b", 1, ttl=-5); assert c.get("b") is None
    def test_default_ttl(self, impl):
        ck = Clock(); c = load(impl)(default_ttl=10, time_fn=ck); c.set("a", 1)
        ck.adv(11); assert c.get("a") is None
    def test_never_expires(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1); ck.adv(1e9)
        assert c.get("a") == 1
    def test_delete_unexpired(self, impl):
        c = load(impl)(); c.set("a", 1); assert c.delete("a") is True; assert c.get("a") is None
    def test_delete_absent(self, impl):
        c = load(impl)(); assert c.delete("x") is False
    def test_delete_expired(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=5); ck.adv(6)
        assert c.delete("a") is False
    def test_len_excludes_expired(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=5); c.set("b", 2)
        ck.adv(6); assert len(c) == 1
    def test_cleanup(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck)
        c.set("a", 1, ttl=5); c.set("b", 2, ttl=5); c.set("c", 3)
        ck.adv(6); assert c.cleanup() == 2; assert len(c) == 1
        assert c.get("a") is None and c.get("c") == 3
    def test_cleanup_reclaims_memory(self, impl):
        ck = Clock(); c = load(impl)(time_fn=ck); c.set("a", 1, ttl=5); ck.adv(6); c.cleanup()
        # internal store must not retain the expired key
        store = getattr(c, "_store", None)
        if store is None:
            store = getattr(c, "_data", None)
        assert "a" not in store
