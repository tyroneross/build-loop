<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- status: RESULTS (unblinded) for pre-registration 2026-06-14-rca-live-test-preregistration.md -->

# Live test RESULTS — RCA discipline upgrade (W1–W3)

Pre-registration: `2026-06-14-rca-live-test-preregistration.md` (committed before any arm ran).
Arms: control = `root-cause-investigator` as-is; treatment = same + W1–W3 levers injected. Both Sonnet.
Judge: independent agent, NOT given the arm→output mapping. **Blinding was PARTIAL, not clean:**
treatment outputs carried labeled fields (creation_path/escape_path/counterfactual/fix_strength_rung)
the control outputs lacked, so the judge could likely INFER which was the upgrade from structure.
We only withheld the explicit label. **Tier deviation:** pre-registered Fable judge was inaccessible
→ judge ran on Opus.

## Arm→label map (revealed after judging)
- S1: RCA-1 = control · RCA-2 = treatment → judge winner RCA-2 = **treatment**
- S2: RCA-1 = treatment · RCA-2 = control → judge winner RCA-1 = **treatment**

## How to read the scores (legend)

- **M1, M2, M4, M5 are grades: higher is better, max 2** (0 = bad/absent, 1 = partial, 2 = best).
  - M1 root cause: 0 missed · 1 partial · 2 fully correct.
  - M2 creation+escape: 0 neither · 1 one · 2 both ("why it existed" AND "why controls missed it").
  - M4 counterfactual: 0 none · 1 stated but weak · 2 stated and would catch THIS bug on the real input.
  - M5 system-level: 0 stopped at surface · 1 reached a system cause.
- **M3 fix-strength = rung 1–7, higher is stronger** (1 docs … 7 eliminate the failure mode).
- **tool_calls / wall = COST, not quality. Lower is better — but only counts when correctness (M1) is equal or better.** A fast wrong answer loses to a slow right one (standing order: Accuracy > Speed > Cost).
- **No statistical significance / correlation / fit** is computed: n=2 is too small. These numbers are directional, not measured signal vs chance.

## Scores (judge, ground-truth from code)
| | M1 root | M2 create+escape | M3 rung | M4 counterfactual | M5 system | tool_calls | wall |
|---|---|---|---|---|---|---|---|
| S1 control   | 2 | 1 | 6 | 1 | 1 | 4  | 102s |
| S1 treatment | 2 | 2 | 6 | 2 | 1 | 13 | 187s |
| S2 control   | 1 | 1 | 6 | 1 | 1 | 89 | 737s |
| S2 treatment | 2 | 2 | 6 | 1 | 1 | 42 | 251s |

## Verdict vs pre-registered decision rule → WIN
- M1 (correctness) no regression: S1 tie (2=2), S2 treatment WINS (2>1). ✓
- Treatment Q strictly > control Q on BOTH subjects (S1: +M2,+M4; S2: +M1,+M2). ✓
- Self-bias guard SATISFIED: treatment carried the correctness win on **S2 — the bug I did NOT diagnose.** ✓
- Speed mixed: S1 treatment +225% calls (slower, but for a real quality gain — the tested levers);
  S2 treatment −53% calls / −66% wall (faster AND more correct). Standing org (Accuracy>Speed>Cost):
  the S1 slow-but-better case still wins; the SPD disqualifier (slower *without* gain) does not bite.

→ **WIN. Pre-registered consequence: authorized to build the RCA plan (W1–W4).**

## Honest nuances (judge-surfaced)
1. On S2 the treatment's edge came largely from CALIBRATION — it flagged the unverified
   `pane close` vs `agent stop` dormant-liveness question as `research_needed` (ptyd Rust source
   absent), instead of overclaiming. That honesty IS the fix-strength/counterfactual discipline working.
2. Treatment was NOT strictly dominant: S2 control (89 calls) surfaced a genuine SECOND leg — the
   unguarded after-snapshot `daemon_panes()` None-collapse — that treatment underweighted. A maximal
   S2 fix MERGES both legs. The levers improved ranking + calibration, not raw coverage.
3. Effort did not track correctness: the cheaper RCA was as-correct (S1) or better-calibrated (S2).
   The levers don't reliably cost more (S1 +cost, S2 −cost).

## Limits
n=2 (directional, not powered). Judge tier Opus not Fable. Treatment via prompt-injection (faithful
proxy for the built prompt). One subject (S1) self-diagnosed — discounted; the win rests on S2.
