import re
def evaluate(expr: str) -> float:
    token_re = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|(\*\*)|([+\-*/()]))")
    tokens=[]; pos=0
    while pos<len(expr):
        m=token_re.match(expr,pos)
        if not m:
            if expr[pos:].strip()=="": break
            raise ValueError("bad char")
        if m.group(1) is not None: tokens.append(("NUMBER",float(m.group(1))))
        else: tokens.append(("OP", m.group(2) or m.group(3)))
        pos=m.end()
    idx=0
    def peek(): return tokens[idx] if idx<len(tokens) else None
    def take():
        nonlocal idx
        t=peek()
        if t is None: raise ValueError("eof")
        idx+=1; return t
    def p_addsub():
        value=p_muldiv()
        while (tok:=peek()) and tok==("OP","+") or (tok:=peek()) and tok==("OP","-"):
            take(); rhs=p_muldiv(); value=value+rhs if tok[1]=="+" else value-rhs
        return value
    def p_muldiv():
        value=p_unary()
        while (tok:=peek()) and tok[0]=="OP" and tok[1] in ("*","/"):
            take(); rhs=p_unary()
            if tok[1]=="*": value=value*rhs
            else:
                if rhs==0: raise ZeroDivisionError("div0")
                value=value/rhs
        return value
    def p_unary():
        tok=peek()
        if tok and tok[0]=="OP" and tok[1] in ("+","-"):
            take(); value=p_unary(); return -value if tok[1]=="-" else +value
        return p_power()
    def p_power():
        base=p_atom(); tok=peek()
        if tok and tok==("OP","**"):
            take(); exponent=p_unary(); return base**exponent
        return base
    def p_atom():
        tok=take()
        if tok[0]=="NUMBER": return tok[1]
        if tok==("OP","("):
            value=p_addsub(); closing=take()
            if closing!=("OP",")"): raise ValueError("paren")
            return value
        raise ValueError("token")
    if not tokens: raise ValueError("empty")
    result=p_addsub()
    if idx!=len(tokens): raise ValueError("trailing")
    return float(result)
