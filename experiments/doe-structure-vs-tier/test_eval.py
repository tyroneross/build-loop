"""Hidden test suite for the arithmetic evaluator DoE. Scores one impl module."""
import importlib, sys, math

# (expr, expected) ; expected=EXC means must raise
EXC = object()
CASES = [
    ("2+3*4", 14), ("2*3+4", 10), ("(2+3)*4", 20), ("8-2-2", 4),
    ("2**3**2", 512), ("-2**2", -4), ("-(2+3)", -5), ("2 + 3 * 4 - 1", 13),
    ("7/2", 3.5), ("10/(2+3)", 2.0), ("--2", 2), ("3*-2", -6),
    ("1.5 * 2", 3.0), ("((1+2)*(3+4))", 21), ("2+2*2**2", 10),
    ("1/0", EXC), ("2+", EXC), ("(1+2", EXC), ("", EXC), ("2 3", EXC),
]

def score(modname):
    m = importlib.import_module(modname)
    ev = getattr(m, "evaluate", None)
    if ev is None:
        return 0, len(CASES), ["no evaluate()"]
    ok = 0; fails = []
    for expr, exp in CASES:
        try:
            r = ev(expr)
            if exp is EXC:
                fails.append(f"{expr!r} should raise, got {r!r}")
            elif isinstance(r, (int, float)) and math.isclose(float(r), float(exp), rel_tol=1e-9, abs_tol=1e-9):
                ok += 1
            else:
                fails.append(f"{expr!r} -> {r!r}, want {exp}")
        except Exception as e:
            if exp is EXC:
                ok += 1
            else:
                fails.append(f"{expr!r} raised {type(e).__name__}, want {exp}")
    return ok, len(CASES), fails

if __name__ == "__main__":
    name = sys.argv[1]
    ok, tot, fails = score(name)
    print(f"{name}: {ok}/{tot}")
    for f in fails[:6]:
        print("   FAIL", f)
