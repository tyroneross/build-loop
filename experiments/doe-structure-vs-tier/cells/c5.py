import re
def evaluate(expr: str) -> float:
    tp=re.compile(r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|(\*\*)|([+\-*/()]))\s*')
    def tok(s):
        t=[];pos=0
        while pos<len(s):
            m=re.match(r'\s+',s[pos:])
            if m: pos+=m.end(); continue
            m=tp.match(s,pos)
            if not m or m.start()!=pos: raise ValueError("char")
            if m.group(1) is not None: t.append(("NUM",float(m.group(1))))
            elif m.group(2) is not None: t.append(("**",None))
            else: t.append((m.group(3),None))
            pos=m.end()
        t.append(("EOF",None)); return t
    if not isinstance(expr,str) or expr.strip()=="": raise ValueError("empty")
    T=tok(expr); p=[0]
    def peek(): return T[p[0]]
    def cons(): x=T[p[0]]; p[0]+=1; return x
    def add():
        l=mul()
        while peek()[0] in ('+','-'):
            o=cons()[0]; r=mul(); l=l+r if o=='+' else l-r
        return l
    def mul():
        l=un()
        while peek()[0] in ('*','/'):
            o=cons()[0]; r=un()
            if o=='*': l*=r
            else:
                if r==0.0: raise ZeroDivisionError("div0")
                l/=r
        return l
    def un():
        t=peek()
        if t[0]=='+': cons(); return +un()
        if t[0]=='-': cons(); return -un()
        return powr()
    def powr():
        b=atom()
        if peek()[0]=='**': cons(); e=un(); return b**e
        return b
    def atom():
        t=peek()
        if t[0]=="NUM": cons(); return t[1]
        if t[0]=='(':
            cons(); v=add()
            if peek()[0]!=')': raise ValueError("paren")
            cons(); return v
        if t[0]=="EOF": raise ValueError("eof")
        raise ValueError("token")
    res=add()
    if peek()[0]!="EOF": raise ValueError("trailing")
    return res
