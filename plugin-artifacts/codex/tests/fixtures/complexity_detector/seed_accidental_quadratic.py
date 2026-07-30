"""Fixture: accidental O(n^2) — nested membership test over the SAME iterable.

SEED:accidental_quadratic@find_dupes — `for x in items: ... if x in items`
is a quadratic scan that a set lookup would make linear.
"""


def find_dupes(items):
    dupes = []
    for x in items:
        # SEED:accidental_quadratic@find_dupes
        if items.count(x) > 1 and x not in dupes:
            dupes.append(x)
    return dupes


def cross_pairs(items):
    pairs = []
    for a in items:
        # SEED:accidental_quadratic@cross_pairs  (nested loop over same iterable name)
        for b in items:
            if a != b:
                pairs.append((a, b))
    return pairs
