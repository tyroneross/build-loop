import re
def evaluate(expr: str) -> float:
    _re = re.compile(r'\s*(?:(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|(\*\*|[+\-*/])|(\()|(\)))')
    def tokenize(s):
        toks=[];pos=0
        while pos < len(s):
            import re as _r
            ws=_r.match(r'\s+', s[pos:])
            if ws: pos+=ws.end(); continue
            m=_re.match(s,pos)
            if not m or m.start()!=pos: raise ValueError("bad char")
            if m.group(1) is not None: toks.append(("NUM",m.group(1)))
            elif m.group(2) is not None: toks.append(("OP",m.group(2)))
            elif m.group(3) is not None: toks.append(("LP","("))
            elif m.group(4) is not None: toks.append(("RP",")"))
            pos=m.end()
        toks.append(("END","")); return toks
    tokens=[]; p=[0]
    def peek(): return tokens[p[0]]
    def consume(): t=tokens[p[0]]; p[0]+=1; return t
    def add():
        l=mul()
        while peek()[0]=="OP" and peek()[1] in ('+','-'):
            o=consume()[1]; r=mul(); l=l+r if o=='+' else l-r
        return l
    def mul():
        l=un()
        while peek()[0]=="OP" and peek()[1] in ('*','/'):
            o=consume()[1]; r=un()
            if o=='*': l*=r
            else:
                if r==0.0: raise ValueError("div0")
                l/=r
        return l
    def un():
        t=peek()
        if t[0]=="OP" and t[1]=='+': consume(); return +un()
        if t[0]=="OP" and t[1]=='-': consume(); return -un()
        return powr()
    def powr():
        b=prim()
        if peek()[0]=="OP" and peek()[1]=='**':
            consume(); e=un(); return b**e
        return b
    def prim():
        t=peek()
        if t[0]=="NUM": consume(); return float(t[1])
        if t[0]=="LP":
            consume(); v=add()
            if peek()[0]!="RP": raise ValueError("paren")
            consume(); return v
        raise ValueError("unexpected")
    if not expr or not expr.strip(): raise ValueError("empty")
    tokens=tokenize(expr); p[0]=0
    res=add()
    if peek()[0]!="END": raise ValueError("trailing")
    return res
