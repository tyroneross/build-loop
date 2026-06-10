#!/usr/bin/env python3
"""Score auditor-tier A/B/C runs. Reads manifest.json + an adjudication file and
computes weighted recall by severity per tier.

Adjudication file (JSON): {
  "artifact": "simple" | "complex",
  "adjudications": {
    "sonnet": {"S1": "yes", "S2": "no", ...},
    "opus":   {...},
    "fable":  {...}
  },
  "extra_findings": {"sonnet": [...], ...}   # findings NOT matching a seeded defect (precision/noise)
}
caught value in {"yes","partial","no"}.
"""
import json
import sys
from pathlib import Path

CAUGHT = {"yes": 1.0, "partial": 0.5, "no": 0.0}
HERE = Path(__file__).parent


def score(manifest_path, adj_path):
    man = json.loads(Path(manifest_path).read_text())
    adj = json.loads(Path(adj_path).read_text())
    artifact = adj["artifact"]
    defects = man["artifacts"][artifact]["defects"]
    weights = man["severity_weights"]
    by_id = {d["id"]: d for d in defects}
    total_w = sum(weights[d["severity"]] for d in defects)
    crit_high = [d["id"] for d in defects if d["severity"] in ("critical", "high")]

    rows = []
    for tier, calls in adj["adjudications"].items():
        got = 0.0
        crit_high_caught = 0.0
        for did, verdict in calls.items():
            w = weights[by_id[did]["severity"]]
            got += w * CAUGHT[verdict]
            if did in crit_high:
                crit_high_caught += CAUGHT[verdict]
        extra = len(adj.get("extra_findings", {}).get(tier, []))
        rows.append({
            "tier": tier,
            "weighted_recall": round(got / total_w, 3),
            "crit_high_recall": round(crit_high_caught / len(crit_high), 3),
            "extra_findings": extra,
            "per_defect": calls,
        })
    return {"artifact": artifact, "total_weight": total_w,
            "crit_high_defects": crit_high, "rows": rows}


if __name__ == "__main__":
    adj = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "adjudication.json")
    print(json.dumps(score(HERE / "manifest.json", adj), indent=2))
