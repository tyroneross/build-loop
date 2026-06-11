def evaluate(expr: str) -> float:
    tokens=[]; i,n=0,len(expr)
    while i<n:
        c=expr[i]
        if c.isspace(): i+=1
        elif c=='*' and i+1<n and expr[i+1]=='*': tokens.append('**'); i+=2
        elif c in '+-*/()': tokens.append(c); i+=1
        elif c.isdigit() or c=='.':
            j=i
            while j<n and expr[j].isdigit(): j+=1
            if j<n and expr[j]=='.':
                j+=1
                while j<n and expr[j].isdigit(): j+=1
            num=expr[i:j]
            if num=='.': raise ValueError("bad num")
            tokens.append(float(num)); i=j
        else: raise ValueError("bad char")
    pos=0
    def peek(): return tokens[pos] if pos<len(tokens) else None
    def adv():
        nonlocal pos; t=tokens[pos]; pos+=1; return t
    def pe():
        v=pt()
        while peek() in ('+','-'):
            o=adv(); r=pt(); v=v+r if o=='+' else v-r
        return v
    def pt():
        v=pu()
        while peek() in ('*','/'):
            o=adv(); r=pu()
            if o=='*': v=v*r
            else:
                if r==0: raise ZeroDivisionError("div0")
                v=v/r
        return v
    def pu():
        if peek() in ('+','-'):
            o=adv(); v=pu(); return -v if o=='-' else v
        return pp()
    def pp():
        b=pa()
        if peek()=='**': adv(); e=pu(); return b**e
        return b
    def pa():
        t=peek()
        if t is None: raise ValueError("eof")
        if isinstance(t,float): return adv()
        if t=='(':
            adv(); v=pe()
            if peek()!=')': raise ValueError("paren")
            adv(); return v
        raise ValueError("token")
    result=pe()
    if pos!=len(tokens): raise ValueError("trailing")
    return float(result)
