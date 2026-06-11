import re
def evaluate(expr: str) -> float:
    TOKEN_RE2 = re.compile(r'\d+\.?\d*|\.\d+|\*\*|[+\-*/()]')
    tokens = TOKEN_RE2.findall(expr)
    rebuilt = ''.join(tokens); check = re.sub(r'\s+','',expr)
    if rebuilt != check: raise ValueError("unrecognized chars")
    if not isinstance(expr,str) or not expr.strip(): raise ValueError("empty")
    state=[0]
    def peek(): return tokens[state[0]] if state[0]<len(tokens) else None
    def consume():
        if state[0]>=len(tokens): raise ValueError("eof")
        t=tokens[state[0]]; state[0]+=1; return t
    def p_add():
        l=p_mul()
        while peek() in ('+','-'):
            o=consume(); r=p_mul(); l=l+r if o=='+' else l-r
        return l
    def p_mul():
        l=p_pow()
        while peek() in ('*','/'):
            o=consume(); r=p_pow()
            if o=='*': l*=r
            else:
                if r==0.0: raise ZeroDivisionError("div0")
                l/=r
        return l
    def p_pow():
        base=p_un()
        if peek()=='**':
            consume(); e=p_pow(); return base**e
        return base
    def p_un():
        if peek()=='-': consume(); return -p_un()
        if peek()=='+': consume(); return +p_un()
        return p_atom()
    def p_atom():
        t=peek()
        if t is None: raise ValueError("eof")
        if t=='(':
            consume(); v=p_add()
            if peek()!=')': raise ValueError("paren")
            consume(); return v
        try: float(t)
        except (ValueError,TypeError): raise ValueError("token")
        consume(); return float(t)
    result=p_add()
    if state[0]<len(tokens): raise ValueError("trailing")
    return result
