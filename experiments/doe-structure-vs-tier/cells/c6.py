def evaluate(expr: str) -> float:
    tokens=[]; i,n=0,len(expr)
    while i<n:
        c=expr[i]
        if c.isspace(): i+=1
        elif c.isdigit() or c=='.':
            j=i; dot=False
            while j<n and (expr[j].isdigit() or expr[j]=='.'):
                if expr[j]=='.':
                    if dot: raise ValueError("num")
                    dot=True
                j+=1
            num=expr[i:j]
            if num=='.': raise ValueError("num")
            tokens.append(('num',float(num))); i=j
        elif c=='*':
            if i+1<n and expr[i+1]=='*': tokens.append(('op','**')); i+=2
            else: tokens.append(('op','*')); i+=1
        elif c in '+-/()': tokens.append(('op',c)); i+=1
        else: raise ValueError("char")
    pos=0
    def peek(): return tokens[pos] if pos<len(tokens) else None
    def take():
        nonlocal pos
        t=peek()
        if t is None: raise ValueError("eof")
        pos+=1; return t
    def p_expr():
        v=p_term()
        while peek() in (('op','+'),('op','-')):
            o=take()[1]; r=p_term(); v=v+r if o=='+' else v-r
        return v
    def p_term():
        v=p_un()
        while peek() in (('op','*'),('op','/')):
            o=take()[1]; r=p_un()
            if o=='*': v=v*r
            else:
                if r==0: raise ZeroDivisionError("div0")
                v=v/r
        return v
    def p_un():
        if peek() in (('op','+'),('op','-')):
            o=take()[1]; v=p_un(); return -v if o=='-' else v
        return p_pow()
    def p_pow():
        b=p_atom()
        if peek()==('op','**'): take(); e=p_un(); return b**e
        return b
    def p_atom():
        k,val=take()
        if k=='num': return val
        if val=='(':
            v=p_expr()
            if take()!=('op',')'): raise ValueError("paren")
            return v
        raise ValueError("token")
    result=p_expr()
    if pos!=len(tokens): raise ValueError("trailing")
    return float(result)
