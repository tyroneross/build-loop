"""Fixture: clean control functions. The detector MUST report ZERO hotspots
for everything in this file (FC-2 / T-05). Each function deliberately stays
under every threshold and avoids every anti-pattern.
"""

__all__ = ["add", "describe", "first_positive", "shared_helper", "use_a", "use_b"]


def add(a, b):
    # Low complexity: no branches.
    return a + b


def describe(x):
    # Single branch — well under the cyclomatic + cognitive thresholds.
    if x is None:
        return "none"
    return "value"


def first_positive(values):
    # Single pass, no nested loop over the same iterable, shallow nesting.
    for v in values:
        if v > 0:
            return v
    return None


def nested_but_different_iterables(rows, cols):
    # Nested loops, but over DIFFERENT iterables — not accidental quadratic.
    out = []
    for r in rows:
        for c in cols:
            out.append((r, c))
    return out


def shared_helper(item):
    # Public (in __all__) AND has >=2 call sites below — not needless indirection.
    return item * 2


def use_a(xs):
    return [shared_helper(x) for x in xs]


def use_b(xs):
    return sum(shared_helper(x) for x in xs)


def two_loops_different_iterables(a, b):
    # Two loops, but over DIFFERENT iterables — not redundant multipass.
    s = 0
    for x in a:
        s += x
    p = 1
    for y in b:
        p *= y
    return s, p
