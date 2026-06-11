def evaluate(expr):
    import ast, operator as op
    if not expr.strip(): raise ValueError("empty")
    ops={ast.Add:op.add,ast.Sub:op.sub,ast.Mult:op.mul,ast.Div:op.truediv,ast.Pow:op.pow,ast.USub:op.neg,ast.UAdd:op.pos}
    def ev(n):
        if isinstance(n,ast.Expression): return ev(n.body)
        if isinstance(n,ast.Constant): return n.value
        if isinstance(n,ast.BinOp): return ops[type(n.op)](ev(n.left),ev(n.right))
        if isinstance(n,ast.UnaryOp): return ops[type(n.op)](ev(n.operand))
        raise ValueError("bad")
    return ev(ast.parse(expr,mode="eval"))
