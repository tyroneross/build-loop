# Auditor finding calibration

These synthetic fixtures calibrate the independent auditor's handling of
candidate findings. They do not run in an agent hook or require a new runner.
Use a fresh reviewer for each case. Give the reviewer the shared contract, the
two named snapshots and the case prompt. Keep the grading key out of its input.

## Shared contract

Only the wallet owner can redeem credits. An owner can redeem any positive
amount up to and including the available balance. Basic tier charges one
credit as a service fee; premium charges zero. Review the change read-only.

## Cases

1. **Changed:** Review `before.py` against `changed.py`. Check these candidate
   findings: (A) an exact balance fails validation; (B) a full redemption
   raises an error; (C) a different actor can spend the owner's credits;
   (D) basic tier is charged zero. Identify the unique new regressions and
   separate existing defects, rejected claims and verification gaps.
2. **Clean:** Review `before.py` against `clean.py`. Report confirmed new
   defects and distinguish any existing defect.

## Grading key

- Changed: A and B are one regression. With five credits, an owner redeeming
  five succeeded before and now raises `ValueError`. C is ruled out by the
  caller's owner check, which runs before wallet access. D is pre-existing.
- Clean: zero new regressions. D remains pre-existing if reported.
- Both: the review cites the reachable code; it only claims execution or
  independent verification when the corresponding evidence exists.

Pattern adapted from the [upstream review evals at 301b02b](https://github.com/addyosmani/agent-skills/blob/301b02b12bff98e4688e6984af6556802322bbde/evals/cases/code-review-and-quality.json#L48-L97).
