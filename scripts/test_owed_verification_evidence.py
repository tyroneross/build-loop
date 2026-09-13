#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Evidence-based discharge of the owed-verification debts.

Every test here corresponds to a finding from the 2026-09-13 audit rounds on
365731c5..815285ab, and every one was observed FAILING against the pre-fix
predicate before the fix landed:

- F1  a cross-vendor entry must point at the round's own output on disk.
- F2  an embedded verdict stamped with ANOTHER run_id is not this run's evidence.
- F3  a non-terminal `status` disqualifies a row whatever its `verdict` says.
- F4  a waiver is granted against a RANGE and lapses when the range widens.
- F6  `clear --all` across several owners must be stated, not inferred.
- F9  the armed range pins HEAD, so a review of a different commit cannot pass.
- F10 the auditor debt discharges from judge-decisions.json, like cross-vendor.
- F13 `check` alone discharges a correctly recorded verdict -- no `clear`.

The fixture repo is a REAL git repo because the range logic resolves refs
through git; a range that cannot be resolved fails open and would pass these
tests for the wrong reason.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent / "owed_verification.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owed_verification as ov  # noqa: E402
from write_run_entry import validators as v  # noqa: E402


def _git(workdir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _run_cli(workdir: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--workdir", str(workdir)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


class _Fixture:
    """A real git repo with a run record, an armed debt, and a review artifact."""

    def __init__(self, tmp: Path, *, host: str = "claude_code") -> None:
        self.root = tmp
        self.reviews = tmp / ".build-loop" / "reviews"
        self.reviews.mkdir(parents=True)
        _git(tmp, "init", "-q", ".")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "t")
        (tmp / "f.txt").write_text("a\n", encoding="utf-8")
        _git(tmp, "add", "f.txt")
        _git(tmp, "commit", "-qm", "base")
        self.base = _git(tmp, "rev-parse", "HEAD")
        (tmp / "f.txt").write_text("a\nb\n", encoding="utf-8")
        _git(tmp, "commit", "-qam", "work")
        self.head = _git(tmp, "rev-parse", "HEAD")
        (self.reviews / "round.md").write_text("the round output\n", encoding="utf-8")
        (self.reviews / "empty.md").write_text("", encoding="utf-8")
        self.host = host
        self.write_state()

    def write_state(self, **run_overrides) -> None:
        run = {
            "run_id": "R1", "date": "2026-09-13", "goal": "g", "outcome": "pass",
            "phases": {}, "filesTouched": ["f.txt"], "diagnosticCommands": [],
            "manualInterventions": [], "active_experimental_artifacts": [],
            "host": self.host, "judge_decisions": [],
        }
        run.update(run_overrides)
        (self.root / ".build-loop" / "state.json").write_text(
            json.dumps({"preBuildSha": self.base, "runs": [run]}), encoding="utf-8"
        )

    def write_judge_file(self, entries: list[dict]) -> None:
        (self.root / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps(entries), encoding="utf-8"
        )

    def arm(self, *verifiers: str, run_id: str = "R1", diff_range: str | None = None):
        return ov.write_manifest(
            self.root,
            run_id=run_id,
            diff_range=diff_range or f"{self.base}..HEAD",
            owed=list(verifiers),
        )

    def cross_vendor_entry(self, **overrides) -> dict:
        entry = {
            "judge_id": "cross-vendor-audit",
            "verdict": "nay",
            "vendor": "openai/gpt-5-codex",
            "run_id": "R1",
            "diff_range": f"{self.base}..{self.head}",
            "recorded_at": "2026-09-13T20:10:20Z",
            "dispatched_by": "claude_code:01CJ6DTVC9Mmxz99H8RaWCwu",
            "evidence": ".build-loop/reviews/round.md",
            "findings_count": {"high": 5, "medium": 3},
        }
        entry.update(overrides)
        return entry

    def auditor_entry(self, **overrides) -> dict:
        entry = {
            "judge_id": "independent-auditor",
            "verdict": "suggest_correction",
            "run_id": "R1",
            "diff_range": f"{self.base}..{self.head}",
        }
        entry.update(overrides)
        return entry


class CheckDischargesRecordedVerdict(unittest.TestCase):
    """F13 + F10 — recording the verdict is what closes the debt."""

    def test_the_exact_entry_the_operator_recorded_discharges_via_check(self) -> None:
        """The verbatim shape from .build-loop/judge-decisions.json entry 17.

        Observed 2026-09-13: this entry satisfied `cross_vendor_present` when
        called directly and `check` STILL returned owed=['cross-vendor-audit'].
        Only `clear` discharged it -- an unverified self-assertion by the same
        agent that owed the review.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            fx.write_judge_file([fx.cross_vendor_entry()])
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "complete", result)
            self.assertIn("cross-vendor-audit", result["discharged_by_evidence"])
            code, _ = _run_cli(fx.root, "check")
            self.assertEqual(code, 0)

    def test_the_auditor_half_discharges_from_the_judge_file_too(self) -> None:
        """F10 — the emitted dispatch command says to append there."""
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("independent-auditor")
            fx.write_judge_file([fx.auditor_entry()])
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "complete", result)

    def test_check_reports_the_field_that_disqualified_each_entry(self) -> None:
        """A rejection with no reason reads exactly like no entry at all.

        The operator's next keystroke is then the waiver -- the path that
        reopens the escape this debt exists to close.
        """
        cases = {
            "same vendor as the host": ({"vendor": "anthropic/claude-opus-5"}, "OWN host family"),
            "no evidence pointer": ({"evidence": None}, "no evidence reference"),
            "evidence not on disk": ({"evidence": ".build-loop/reviews/nope.md"}, "not a regular file"),
            "evidence is a junk token": ({"evidence": "zz"}, "not a regular file"),
            "session id too short to be one": ({"evidence": None, "session_id": "x"}, "not a resolvable session id"),
            "evidence outside the repo": ({"evidence": "/etc/hostname"}, "outside the repository"),
            "evidence is an empty file": ({"evidence": ".build-loop/reviews/empty.md"}, "EMPTY file"),
            "unfinished round": ({"status": "pending"}, "has not finished"),
            "another diff": ({"diff_range": "deadbeef..cafebabe"}, "not the armed range"),
        }
        for label, (override, expected) in cases.items():
            with self.subTest(label), TemporaryDirectory() as td:
                fx = _Fixture(Path(td))
                fx.arm("cross-vendor-audit")
                entry = fx.cross_vendor_entry(**override)
                if override.get("evidence") is None and "evidence" in override:
                    entry.pop("evidence")
                fx.write_judge_file([entry])
                result = ov.check_manifest(fx.root)
                self.assertEqual(result["status"], "incomplete", result)
                reasons = " ".join(
                    str(r.get("reason"))
                    for e in result["evidence"]
                    for r in e.get("rejected_evidence") or []
                )
                self.assertIn(expected, reasons, f"{label}: {reasons}")


class EvidenceRequired(unittest.TestCase):
    """F1 — the vendor string is metadata the recorder asserts about itself."""

    def test_a_vendor_claim_with_no_artifact_does_not_discharge(self) -> None:
        entry = {
            "judge_id": "cross-vendor-audit", "verdict": "yay",
            "vendor": "openai/gpt-5-codex", "run_id": "R1",
        }
        # PLANTED DEFECT (pre-fix predicate): the vendor string alone passed.
        self.assertIsNotNone(v.vendor_provider(entry["vendor"]))
        self.assertTrue(v.rendered_verdict(entry))
        # Post-fix: an unauthenticated claim needs a pointer to the round output.
        self.assertFalse(v.cross_vendor_present([entry], "claude_code"))

    def test_an_opaque_session_id_is_accepted_as_named(self) -> None:
        """This process cannot resolve another host's session store.

        It is still a specific claim a later audit can chase, which is more than
        the vendor string alone was.
        """
        entry = {
            "judge_id": "cross-vendor-audit", "verdict": "yay",
            "vendor": "openai/gpt-5-codex", "run_id": "R1",
            "codex_session_id": "01JQ8Z3K4M5N6P7Q8R9S",
        }
        self.assertTrue(v.cross_vendor_present([entry], "claude_code"))


class NonTerminalStatus(unittest.TestCase):
    """F3 + F11 + F12 — the lifecycle beats the verdict, and the docstring says so."""

    def test_pending_status_rejects_whatever_the_verdict_says(self) -> None:
        for verdict in ("yay", "approve", "nay", "suggest_correction"):
            with self.subTest(verdict):
                self.assertFalse(
                    v.rendered_verdict({"verdict": verdict, "status": "pending"})
                )

    def test_every_non_terminal_status_is_rejected(self) -> None:
        """The hardcoded names come FIRST on purpose.

        Iterating only `v.NON_VERDICT_STATUSES` makes the loop body vanish when
        the constant is emptied, so the test passes on the mutation it exists to
        catch. A test must not depend on its own subject for its inputs.
        """
        for status in ("pending", "running", "queued", "dispatched"):
            with self.subTest(status):
                self.assertIn(status, v.NON_VERDICT_STATUSES)
                self.assertFalse(
                    v.rendered_verdict({"verdict": "yay", "status": status})
                )
        for status in v.NON_VERDICT_STATUSES:
            with self.subTest(status):
                self.assertFalse(
                    v.rendered_verdict({"verdict": "yay", "status": status}),
                    f"{status!r} claims a result from a round that says it is unfinished",
                )

    def test_packet_emitted_still_passes(self) -> None:
        """The 2026-09-13 regression this must not reintroduce.

        `audit_record_verdict.py` fills the verdict IN PLACE and leaves the
        status as `packet_emitted`; treating that as non-terminal rejected 103
        real rows in this repo's own ledger.
        """
        self.assertNotIn("packet_emitted", v.NON_VERDICT_STATUSES)
        self.assertTrue(
            v.rendered_verdict({"verdict": "yay", "status": "packet_emitted"})
        )

    def test_the_constant_is_live_not_decorative(self) -> None:
        """F12 — NON_VERDICT_STATUSES was dead after the previous rewrite."""
        probe = next(iter(v.NON_VERDICT_STATUSES))
        self.assertIn(
            "has not finished",
            str(v.verdict_rejection({"verdict": "yay", "status": probe})),
        )


class EmbeddedVerdictScoping(unittest.TestCase):
    """F2 — only the external judge file filtered evidence by run identity."""

    def test_an_embedded_verdict_stamped_with_another_run_is_skipped(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            foreign = fx.cross_vendor_entry(run_id="SOME-OTHER-RUN")
            fx.write_state(judge_decisions=[foreign])
            fx.arm("cross-vendor-audit")
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "incomplete", result)

    def test_an_unstamped_embedded_verdict_is_still_this_runs_own(self) -> None:
        """The overwhelming majority of historical rows carry no run_id.

        Dropping them would re-arm the repository's whole history for no gain.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            entry = fx.cross_vendor_entry()
            entry.pop("run_id")
            fx.write_state(judge_decisions=[entry])
            fx.arm("cross-vendor-audit")
            self.assertEqual(ov.check_manifest(fx.root)["status"], "complete")


class RangeIsPinnedAtArmTime(unittest.TestCase):
    """F9 — `<sha>..HEAD` stores a question, re-answered at every read."""

    def test_the_armed_range_holds_no_moving_endpoint(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            payload = fx.arm("cross-vendor-audit")
            self.assertNotIn("HEAD", payload["diff_range"])
            self.assertEqual(payload["diff_range"], f"{fx.base}..{fx.head}")

    def test_a_review_of_an_earlier_commit_cannot_discharge_a_later_debt(self) -> None:
        """The exact under-arming the pre-fix code allowed.

        The reviewer copies the manifest's own printed range, `<base>..HEAD`.
        Unpinned, both sides re-resolve to whatever HEAD has become, so they
        agree by construction and the guard can never fire -- a review that
        never saw commit B discharges a debt that includes it.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            fx.write_judge_file([
                fx.cross_vendor_entry(diff_range=f"{fx.base}..HEAD")
            ])
            (fx.root / "f.txt").write_text("a\nb\nUNREVIEWED\n", encoding="utf-8")
            _git(fx.root, "commit", "-qam", "B: not in the reviewed range")
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "incomplete", result)
            self.assertIn("cross-vendor-audit", result["owed"])


class WaiverIsScopedToTheRange(unittest.TestCase):
    """F4 — a waiver recorded only (run_id, verifier), so it exempted the RUN."""

    def test_widening_the_diff_invalidates_the_waiver(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            ov.clear_verifiers(
                fx.root, verifiers=["cross-vendor-audit"], run_id="R1",
                reason="no peer host reachable",
            )
            # Same run, more code. The waiver was granted before this existed.
            (fx.root / "f.txt").write_text("a\nb\nNEW\n", encoding="utf-8")
            _git(fx.root, "commit", "-qam", "C: added after the waiver")
            new_head = _git(fx.root, "rev-parse", "HEAD")
            self.assertEqual(
                ov._cleared_for_run(fx.root, "R1", f"{fx.base}..{new_head}"),
                set(),
                "a waiver granted at one range must not cover a wider one",
            )

    def test_the_same_range_stays_waived(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            ov.clear_verifiers(
                fx.root, verifiers=["cross-vendor-audit"], run_id="R1",
                reason="no peer host reachable",
            )
            self.assertEqual(
                ov._cleared_for_run(fx.root, "R1", f"{fx.base}..{fx.head}"),
                {"cross-vendor-audit"},
            )

    def test_the_waiver_records_the_range_it_was_granted_against(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            ov.clear_verifiers(
                fx.root, verifiers=["cross-vendor-audit"], run_id="R1", reason="why",
            )
            state = json.loads(
                (fx.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
            )
            waiver = state["review_cleared_verifiers"]["R1"]["cross-vendor-audit"]
            self.assertEqual(waiver["diff_range"], f"{fx.base}..{fx.head}")


class ClearBoundaries(unittest.TestCase):
    """F6 + F10 — a sweep and an unbacked waiver are both DECISIONS."""

    def test_all_across_several_owners_is_refused_without_the_explicit_flag(self) -> None:
        """`--reason` is supplied so ONLY the owner boundary is under test.

        Without it the waiver-reason guard also refuses this command, and the
        test would pass with the owner boundary deleted -- a guard test that
        certifies the hole it was written to catch. Observed: planting the
        pre-fix `--all` code left this green until `--reason` was added.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit", run_id="R1")
            fx.arm("cross-vendor-audit", run_id="R2")
            code, out = _run_cli(fx.root, "clear", "--all", "--reason", "peer offline")
            self.assertEqual(code, 2, out)
            self.assertIn("--i-mean-every-run", out)
            self.assertEqual(ov.check_manifest(fx.root)["status"], "incomplete")

    def test_the_explicit_sweep_requires_a_reason(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("scope-auditor", run_id="R1")
            fx.arm("scope-auditor", run_id="R2")
            code, out = _run_cli(fx.root, "clear", "--all", "--i-mean-every-run")
            self.assertEqual(code, 2, out)
            self.assertIn("--reason", out)

    def test_a_single_owner_manifest_still_clears_unchanged(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("scope-auditor", run_id="R1")
            code, out = _run_cli(fx.root, "clear", "--all")
            self.assertEqual(code, 0, out)

    def test_clearing_an_unbacked_managed_debt_requires_a_reason(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            code, out = _run_cli(
                fx.root, "clear", "--verifier", "cross-vendor-audit", "--run-id", "R1"
            )
            self.assertEqual(code, 2, out)
            self.assertIn("WAIVER", out)

    def test_a_verdict_backed_clear_needs_no_reason(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            fx.write_judge_file([fx.cross_vendor_entry()])
            code, out = _run_cli(
                fx.root, "clear", "--verifier", "cross-vendor-audit", "--run-id", "R1"
            )
            self.assertEqual(code, 0, out)


class UnreadableManifestBlocksANonAdvisoryClose(unittest.TestCase):
    """F5 — any exception became 'no gap' inside the gate against that move."""

    def test_an_unreadable_check_is_a_gap_not_a_pass(self) -> None:
        import run_close_lint

        original = ov.check_manifest
        try:
            ov.check_manifest = lambda *a, **k: (_ for _ in ()).throw(
                OSError("state.json is locked")
            )
            with TemporaryDirectory() as td:
                gap = run_close_lint._owed_verification_gap(Path(td))
            self.assertIsNotNone(gap, "an unreadable manifest must not read as clean")
            self.assertTrue(gap["unreadable"])
            self.assertEqual(gap["status"], "incomplete")
            self.assertIn("state.json is locked", gap["error"])
        finally:
            ov.check_manifest = original

    def test_a_non_dict_return_is_also_a_gap(self) -> None:
        import run_close_lint

        original = ov.check_manifest
        try:
            ov.check_manifest = lambda *a, **k: None
            with TemporaryDirectory() as td:
                gap = run_close_lint._owed_verification_gap(Path(td))
            self.assertIsNotNone(gap)
            self.assertTrue(gap["unreadable"])
        finally:
            ov.check_manifest = original

    def test_the_envelope_blocks_and_names_what_it_could_not_read(self) -> None:
        import run_close_lint

        envelope = run_close_lint._apply_owed_verification(
            Path("/nonexistent"),
            {"status": "recorded", "run_id": "R1"},
        )
        # /nonexistent has no manifest, so this is the clean path.
        self.assertEqual(envelope["status"], "recorded")

        original = run_close_lint._owed_verification_gap
        try:
            run_close_lint._owed_verification_gap = lambda wd, **kw: {
                "status": "incomplete", "owed": ["unknown"], "debts": [],
                "owed_runs": {}, "dispatch_commands": {}, "unreadable": True,
                "error": "OSError: boom",
            }
            envelope = run_close_lint._apply_owed_verification(
                Path("/nonexistent"), {"status": "recorded", "run_id": "R1"}
            )
            self.assertEqual(envelope["status"], "review_owed")
            self.assertTrue(envelope["review_incomplete"])
            self.assertIn("boom", envelope["reason"])
        finally:
            run_close_lint._owed_verification_gap = original


class TheProductionArmingPathStillArms(unittest.TestCase):
    """The gap that let a signature mismatch disarm the whole gate silently.

    Every other test in this file arms via `write_manifest`, which is the CLI
    path. Production arms via `enforce_for_run_record`, which is wrapped in a
    fail-open `except` -- so a TypeError inside the predicate produced NO
    manifest, and a missing manifest is indistinguishable from "nothing owed".
    Observed during this very build on 2026-09-13: `_range_scoped` was called
    with three arguments and defined with two; 26 tests stayed green and the
    gate armed nothing. The pre-existing suite caught it; this file had not.
    """

    def test_a_code_touching_run_with_no_verdict_arms_through_enforce(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            record = json.loads(
                (fx.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
            )["runs"][0]
            record["auditor_status"] = "not-run:parent-must-dispatch"
            manifest = ov.enforce_for_run_record(
                fx.root, record, written_by="test", diff_range=f"{fx.base}..HEAD"
            )
            self.assertIsNotNone(
                manifest,
                "the production arming path produced no manifest; a fail-open "
                "exception here reads exactly like 'nothing owed'",
            )
            self.assertIn("independent-auditor", manifest["owed"])
            self.assertNotIn("HEAD", manifest["diff_range"])

    def test_enforce_discharges_when_the_verdict_is_on_record(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            record = json.loads(
                (fx.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
            )["runs"][0]
            record["judge_decisions"] = [fx.auditor_entry()]
            fx.write_state(judge_decisions=[fx.auditor_entry()])
            self.assertIsNone(
                ov.enforce_for_run_record(
                    fx.root, record, written_by="test", diff_range=f"{fx.base}..HEAD"
                )
            )


class SecondRoundRegressions(unittest.TestCase):
    """Findings both 2026-09-13 review rounds returned on the FIRST fix pass.

    Every one of these is a path where the first pass resolved toward LESS debt,
    which is the single direction this module is not allowed to be wrong in.
    """

    def test_widening_a_runs_diff_re_points_its_debt(self) -> None:
        """The reconcile's own regression: a debt kept its FIRST range forever.

        A run that grew from base..A to base..B still carried base..A, so a
        review of the narrower diff discharged it, unlinked the manifest, and
        set review_incomplete=False while the commits added afterwards shipped
        unreviewed. Before the reconcile existed the same state still required
        an explicit, logged waiver.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            fx.write_judge_file([fx.cross_vendor_entry()])
            (fx.root / "f.txt").write_text("a\nb\nUNREVIEWED\n", encoding="utf-8")
            _git(fx.root, "commit", "-qam", "B")
            wide = fx.arm("cross-vendor-audit")
            self.assertEqual(
                wide["debts"][0]["diff_range"],
                f"{fx.base}..{_git(fx.root, 'rev-parse', 'HEAD')}",
                "re-arming at a wider range must re-point the existing row",
            )
            self.assertEqual(ov.check_manifest(fx.root)["status"], "incomplete")

    def test_a_manifest_naming_no_debt_view_is_unreadable_not_empty(self) -> None:
        """`{}` and `{"status": "incomplete"}` both read as a COMPLETE review."""
        for shape in ({}, {"status": "incomplete"}, {"run_id": "R1"}):
            with self.subTest(str(shape)), TemporaryDirectory() as td:
                fx = _Fixture(Path(td))
                (fx.root / ".build-loop" / "owed-verification.json").write_text(
                    json.dumps(shape), encoding="utf-8"
                )
                result = ov.check_manifest(fx.root)
                self.assertEqual(result["status"], "incomplete", result)

    def test_every_range_endpoint_is_pinned_not_only_HEAD(self) -> None:
        """`HEAD~1..HEAD` kept a symbolic base that re-resolved at every read."""
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            payload = fx.arm("cross-vendor-audit", diff_range="HEAD~1..HEAD")
            self.assertNotIn("HEAD", payload["diff_range"])
            self.assertTrue(ov._is_pinned(payload["diff_range"]))
            self.assertTrue(payload["debts"][0]["pinned"])

    def test_a_range_that_still_moves_cannot_be_discharged_by_evidence(self) -> None:
        """A transient rev-parse failure made both sides agree by construction.

        Simulated by planting the unpinned row a failed pin would have written.
        The test is MOVEMENT: git can still re-resolve `<base>..HEAD`, so both
        sides resolve at check time and the range guard can never fire. A range
        whose endpoints git cannot resolve at all is literal on both sides and
        is deliberately still comparable -- see `evaluate_debt`.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            manifest_path = fx.root / ".build-loop" / "owed-verification.json"
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["debts"][0]["diff_range"] = f"{fx.base}..HEAD"
            data["debts"][0]["pinned"] = False
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            fx.write_judge_file([
                fx.cross_vendor_entry(diff_range=f"{fx.base}..HEAD")
            ])
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "incomplete", result)
            self.assertIn("still resolves to a different", str(result["evidence"]))

    def test_a_range_git_cannot_resolve_is_still_comparable(self) -> None:
        """Literal on both sides, so it does not move and does not need pinning.

        Refusing it would make every debt armed outside a resolvable repo
        waiver-only, which pushes the operator at the waiver for a range that
        was never ambiguous.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit", diff_range="tag-a..tag-b")
            fx.write_judge_file([fx.cross_vendor_entry(diff_range="tag-a..tag-b")])
            self.assertEqual(ov.check_manifest(fx.root)["status"], "complete")

    def test_an_unknown_range_is_waiver_only(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit", diff_range="unknown")
            fx.write_judge_file([fx.cross_vendor_entry()])
            self.assertEqual(ov.check_manifest(fx.root)["status"], "incomplete")

    def test_an_unstamped_auditor_verdict_cannot_discharge_an_armed_debt(self) -> None:
        """Arming stays lenient; DISCHARGE does not.

        The arming side accepts an unstamped verdict because 139 of 143 real
        auditor rows carry no range. Once a debt is armed its dispatch command
        spells run_id and diff_range, so an unstamped entry is evidence the
        instruction was not followed -- and without this split an older verdict
        for the same run discharged a debt armed over code written after it.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("independent-auditor")
            entry = fx.auditor_entry()
            entry.pop("diff_range")
            fx.write_judge_file([entry])
            result = ov.check_manifest(fx.root)
            self.assertEqual(result["status"], "incomplete", result)
            self.assertIn("names no diff_range", str(result["evidence"]))

    def test_arming_failure_leaves_a_debt_not_a_clean_run(self) -> None:
        """A log line is not a mechanism: nothing reads the audit log.

        This is the class of the `_range_scoped` arity bug found earlier in this
        same build, where the gate armed nothing and 26 green tests said so.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            record = json.loads(
                (fx.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
            )["runs"][0]
            original = ov.owed_verifiers_for_record
            try:
                ov.owed_verifiers_for_record = lambda *a, **k: (
                    _ for _ in ()
                ).throw(TypeError("planted: the predicate cannot run"))
                manifest = ov.enforce_for_run_record(
                    fx.root, record, written_by="test",
                    diff_range=f"{fx.base}..HEAD",
                )
            finally:
                ov.owed_verifiers_for_record = original
            self.assertIsNotNone(manifest, "arming failure produced no manifest")
            self.assertIn(ov.ARMING_FAILED_VERIFIER, manifest["owed"])
            self.assertEqual(ov.check_manifest(fx.root)["status"], "incomplete")

    def test_the_in_process_clear_api_enforces_the_waiver_reason(self) -> None:
        """The guard lived in argparse, so every in-process caller bypassed it."""
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            with self.assertRaises(ValueError) as ctx:
                ov.clear_verifiers(
                    fx.root, verifiers=["cross-vendor-audit"], run_id="R1"
                )
            self.assertIn("WAIVER", str(ctx.exception))
            self.assertEqual(ov.check_manifest(fx.root)["status"], "incomplete")

    def test_a_concurrent_re_arm_is_not_erased_by_older_evidence(self) -> None:
        """compare-and-swap on the range.

        `check_manifest` evaluates outside the lock and clears inside it, so a
        re-arm landing between the two would otherwise be removed on the
        strength of evidence gathered against the previous range.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            debt = ov.check_manifest(fx.root, reconcile=False)["debts"][0]
            (fx.root / "f.txt").write_text("a\nb\nNEW\n", encoding="utf-8")
            _git(fx.root, "commit", "-qam", "C")
            fx.arm("cross-vendor-audit")  # re-armed at the wider range
            ov.clear_verifiers(
                fx.root, verifiers=["cross-vendor-audit"], run_id="R1",
                evidence_discharge=True, record_tombstone=False,
                expected_ranges={"cross-vendor-audit": debt["diff_range"]},
            )
            self.assertEqual(
                ov.check_manifest(fx.root, reconcile=False)["status"],
                "incomplete",
                "a stale-range discharge must not remove the re-armed debt",
            )

    def test_the_auditor_dispatch_command_spells_run_id(self) -> None:
        """The agent's own envelope carries no run_id, and the file filters on it.

        So the round ran, the verdict landed, and `check` reported the debt owed
        with an EMPTY rejection list -- indistinguishable from no entry at all.
        """
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            payload = fx.arm("independent-auditor")
            command = payload["dispatch_commands"]["independent-auditor"]
            for field in ("judge_id", "verdict", "run_id", "diff_range"):
                self.assertIn(field, command, f"{field} missing from the command")

    def test_the_advisory_close_check_does_not_mutate(self) -> None:
        """A hook that REPORTS on state must not change what it reports on."""
        import run_close_lint

        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            fx.arm("cross-vendor-audit")
            fx.write_judge_file([fx.cross_vendor_entry()])
            manifest_path = fx.root / ".build-loop" / "owed-verification.json"
            before = manifest_path.read_text(encoding="utf-8")
            run_close_lint._owed_verification_gap(fx.root, reconcile=False)
            self.assertTrue(manifest_path.exists(), "advisory read deleted the manifest")
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), before)


class DispatchCommandProducesADischargingEntry(unittest.TestCase):
    """The emitted command must spell every field the predicate now requires.

    A command that produces an entry which cannot discharge the debt is a round
    that ran and still reads as un-run, with the waiver as the only visible exit.
    This file already carries that defect twice in its history.
    """

    def test_the_template_spells_evidence(self) -> None:
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td))
            payload = fx.arm("cross-vendor-audit")
            command = payload["dispatch_commands"]["cross-vendor-audit"]
            for field in ("judge_id", "verdict", "vendor", "run_id", "diff_range", "evidence"):
                self.assertIn(field, command, f"{field} missing from the emitted command")

    def test_the_codex_host_override_spells_it_too(self) -> None:
        """The branch where this debt matters most, and which drifted before."""
        with TemporaryDirectory() as td:
            fx = _Fixture(Path(td), host="codex")
            overrides = ov._host_dispatch_overrides(
                {"host": "codex"}, f"{fx.base}..{fx.head}", "R1"
            )
            self.assertIn("evidence", overrides["cross-vendor-audit"])


if __name__ == "__main__":
    unittest.main()
