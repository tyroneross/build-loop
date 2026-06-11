import re
def evaluate(expr: str) -> float:
    TOKEN_RE=re.compile(r'\d+\.?\d*|\.\d+|[+\-*/^()]|\*\*')
    def tokenize(s):
        raw=TOKEN_RE.findall(s)
        cleaned=re.sub(r'\s','',s)
        if ''.join(raw)!=cleaned: raise ValueError("chars")
        return raw
    if not expr or not expr.strip(): raise ValueError("empty")
    tokens=tokenize(expr)
    for i in range(len(tokens)-1):
        if re.fullmatch(r'\d+\.?\d*|\.\d+',tokens[i]) and re.fullmatch(r'\d+\.?\d*|\.\d+',tokens[i+1]):
            raise ValueError("two numbers")
    pos=[0]
    def peek(): return tokens[pos[0]] if pos[0]<len(tokens) else None
    def cons(exp=None):
        if pos[0]>=len(tokens): raise ValueError("eof")
        t=tokens[pos[0]]
        if exp is not None and t!=exp: raise ValueError("expect")
        pos[0]+=1; return t
    def p_expr():
        l=p_term()
        while peek() in ('+','-'):
            o=cons(); r=p_term(); l=l+r if o=='+' else l-r
        return l
    def p_term():
        l=p_factor()
        while peek() in ('*','/'):
            o=cons(); r=p_factor()
            if o=='/':
                if r==0: raise ZeroDivisionError("div0")
                l=l/r
            else: l=l*r
        return l
    def p_factor():
        base=p_unary()
        if peek()=='**':
            cons('**'); e=p_factor(); return base**e
        return base
    def p_unary():
        if peek()=='-': cons('-'); return -p_unary()
        if peek()=='+': cons('+'); return +p_unary()
        return p_primary()
    def p_primary():
        t=peek()
        if t is None: raise ValueError("eof")
        if t=='(':
            cons('('); v=p_expr()
            if peek()!=')': raise ValueError("paren")
            cons(')'); return v
        if re.fullmatch(r'\d+\.?\d*|\.\d+',t): cons(); return float(t)
        raise ValueError("token")
    result=p_expr()
    if pos[0]!=len(tokens): raise ValueError("trailing")
    return result
