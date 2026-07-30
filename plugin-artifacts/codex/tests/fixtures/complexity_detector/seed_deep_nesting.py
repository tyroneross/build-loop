"""Fixture: deep statement nesting beyond the depth threshold.

SEED:deep_nesting@deeply_nested — innermost assignment is 5 blocks deep.
"""


def deeply_nested(matrix):
    total = 0
    for row in matrix:
        if row:
            for cell in row:
                if cell is not None:
                    if cell > 0:
                        # SEED:deep_nesting@deeply_nested  (depth 5)
                        total += cell
    return total
