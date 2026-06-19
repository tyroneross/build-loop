# Mini-RCA — lightweight L1

> For low-risk issues where a full RCA is too heavy but a fix alone is not enough. Do not over-investigate; do not close on "be more careful / human error / agent error / edge case."

Identify: (1) what happened, (2) why, (3) why it wasn't caught, (4) the smallest durable lever that prevents recurrence.

## 1. Symptom (observation only)
Observed · Expected · Actual · Impact.

## 2. Failure class (pick one primary)
Spec gap · Context gap · Execution gap · Tool gap · Verification gap · State gap · Review gap · Ownership gap.

## 3. Four whys (brief; label each FACT/ASSUMPTION/INFERENCE/UNKNOWN — FACT only if checked)
1. Why did the bad condition exist? 2. Why did that happen? 3. Why was it not caught? 4. Why would it recur if nothing changes?
**Tree-escalation:** if a SECOND independent contributor appears, stop — this is not a linear chain; escalate to L2 (`01-rca.md` causal map).

## 4. Smallest durable fix (must change the system)
e.g. add acceptance criterion · add test · add lint/type/check gate · add template field · add preflight command · add review-checklist item · add tool-wrapper validation · add memory/rule · add rollback/snapshot · clarify ownership. NOT "be more careful."
State **Lever** + **Actuator** (what makes it fire) + Verification. Owner optional (may be a gate/hook).

## 5. Output
`# Mini-RCA` → Bottom line (cause + durable fix, 1 sentence) → Symptom → Failure class (+why) → Four whys table (Why | Answer | Label | Confidence) → Smallest durable fix (Lever/Actuator/[Owner]/Verification) → Follow-up (Does this need full RCA? if yes, why?).
Density: keep it short — if it's growing past a screen, it's an L2.
