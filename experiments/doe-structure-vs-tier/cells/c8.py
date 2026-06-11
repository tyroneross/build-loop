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
            text=expr[i:j]
            if text=='.': raise ValueError("num")
            tokens.append(('NUM',float(text))); i=j
        elif c=='*':
            if i+1<n and expr[i+1]=='*': tokens.append(('OP','**')); i+=2
            else: tokens.append(('OP','*')); i+=1
        elif c in '+-/()': tokens.append(('OP',c)); i+=1
        else: raise ValueError("char")
    pos=[0]
    def peek(): return tokens[pos[0]] if pos[0]<len(tokens) else (None,None)
    def take_op(*ops):
        k,v=peek()
        if k=='OP' and v in ops: pos[0]+=1; return v
        return None
    def p_add():
        v=p_mul()
        while True:
            o=take_op('+','-')
            if o is None: return v
            r=p_mul(); v=v+r if o=='+' else v-r
    def p_mul():
        v=p_un()
        while True:
            o=take_op('*','/')
            if o is None: return v
            r=p_un()
            if o=='*': v=v*r
            else:
                if r==0: raise ZeroDivisionError("div0")
                v=v/r
    def p_un():
        o=take_op('+','-')
        if o is not None:
            v=p_un(); return -v if o=='-' else v
        return p_pow()
    def p_pow():
        b=p_atom()
        if take_op('**'):
            e=p_un()
            if b==0 and e<0: raise ZeroDivisionError("0negpow")
            return b**e
        return b
    def p_atom():
        k,val=peek()
        if k=='NUM': pos[0]+=1; return val
        if k=='OP' and val=='(':
            pos[0]+=1; v=p_add()
            if take_op(')') is None: raise ValueError("paren")
            return v
        if k is None: raise ValueError("eof")
        raise ValueError("token")
    if not tokens: raise ValueError("empty")
    result=p_add()
    if pos[0]!=len(tokens): raise ValueError("trailing")
    return float(result)
