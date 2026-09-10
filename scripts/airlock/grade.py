#!/usr/bin/env python3
"""Grade a rule set against the adversarial corpus. Prints confusion + misses."""
import json, sys, importlib.util, os

def load_rules(path):
    spec = importlib.util.spec_from_file_location("rules", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def main(rules_path, corpus_path):
    R = load_rules(rules_path)
    c = json.load(open(corpus_path))
    tp=fn=tn=fp=0; misses=[]; falsealarms=[]
    for case in c["positive"]:
        v = R.classify(case["text"])
        if v == "none": fn+=1; misses.append((case["id"], case["origin"], case["tier"]))
        else: tp+=1
    for case in c["negative"]:
        v = R.classify(case["text"])
        if v == "none": tn+=1
        else: fp+=1; falsealarms.append((case["id"], case["why"], v))
    prec = tp/(tp+fp) if tp+fp else 0.0
    rec  = tp/(tp+fn) if tp+fn else 0.0
    print(f"recall    {rec:.0%}  ({tp}/{tp+fn} attacks caught)")
    print(f"precision {prec:.0%}  ({fp} false alarms on {tn+fp} benign)")
    if misses:
        print("\nMISSED ATTACKS:")
        for i,o,t in misses: print(f"  {i} [{t}] {o}")
    if falsealarms:
        print("\nFALSE ALARMS:")
        for i,w,v in falsealarms: print(f"  {i} -> {v} :: {w}")
    return 0 if not misses and not falsealarms else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
