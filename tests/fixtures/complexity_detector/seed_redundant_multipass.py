"""Fixture: two separate top-level loops over the SAME iterable in one function,
collapsible to a single pass (no data dependency forbidding fusion).

SEED:redundant_multipass@summarize — loop 1 sums, loop 2 counts; both walk
`values` independently and could be one pass.
"""


def summarize(values):
    total = 0
    for v in values:
        # SEED:redundant_multipass@summarize  (pass 1)
        total += v

    count = 0
    for v in values:
        # SEED:redundant_multipass@summarize  (pass 2 over same iterable)
        if v > 0:
            count += 1

    return total, count
