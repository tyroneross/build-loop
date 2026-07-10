#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for security_scan.py. Stdlib unittest only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import security_scan  # noqa: E402

_SCANNER = Path(__file__).parent / "security_scan.py"

# A synthetic, obviously-fake GitHub PAT built by concatenation so the literal
# token never appears verbatim in source (keeps the mock/secret scanners quiet).
# Matches _PROVIDER_KEY_PATTERNS ghp_[A-Za-z0-9]{36,} → HIGH check-A finding.
_FAKE_GHP = "ghp_" + "A" * 36
_SECRET_LINE = f'const token = "{_FAKE_GHP}";\n'


class TestCheckA(unittest.TestCase):
    def _run(self, lines):
        return security_scan.check_A_secrets(Path("fake.ts"), lines, root_path=None)

    def test_openai_key_detected(self):
        lines = ['const apiKey = "sk-abcdefghijklmnopqrstuvwxyz1234567890ab";\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0, "Should flag hardcoded OpenAI key")
        self.assertEqual(findings[0]["severity"], "HIGH")
        self.assertIn("A07", findings[0]["owasp_ids"])

    def test_env_lookup_not_flagged(self):
        lines = ["const apiKey = process.env.OPENAI_API_KEY;\n"]
        self.assertEqual(self._run(lines), [])

    def test_placeholder_not_flagged(self):
        lines = ['const apiKey = "your-api-key-here";\n']
        self.assertEqual(self._run(lines), [])

    def test_generic_secret_assignment(self):
        lines = ['const CLIENT_SECRET = "z9mLpQrXwAbCdEfGhIjKlMn";\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0, "Should flag hardcoded client secret")

    def test_brevo_key_detected(self):
        lines = ['const key = "xkeysib-abcdefgh1234567890abcdef12345678";\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0)

    def test_commented_line_skipped(self):
        lines = ['// const apiKey = "sk-abcdefghijklmnopqrstuvwxyz1234";\n']
        # Comments are still flagged for secrets (a secret in a comment is still exposed)
        # But single-line comment lines ARE skipped by the check
        findings = self._run(lines)
        self.assertEqual(findings, [], "Should skip commented lines")

    def test_nosec_suppresses(self):
        lines = ['const apiKey = "sk-abcdefghijklmnopqrstuvwxyz1234567890";  // nosec: test fixture\n']
        self.assertEqual(self._run(lines), [])


class TestCheckB(unittest.TestCase):
    def _run(self, lines):
        return security_scan.check_B_secret_in_logs(Path("fake.ts"), lines)

    def test_token_variable_in_log(self):
        lines = ['  console.log("Token response body:", accessToken);\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0, "Should flag logging of accessToken")
        self.assertEqual(findings[0]["severity"], "HIGH")
        self.assertIn("A09", findings[0]["owasp_ids"])

    def test_sensitive_label_and_var(self):
        lines = ["  console.log('Token response body:', text);\n"]
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0, "Should flag label 'Token...' + second arg")

    def test_error_string_only_not_flagged(self):
        # The key is in a string literal, not a variable — safe
        lines = ['  console.error("[contact] TURNSTILE_SECRET_KEY absent — rejecting");\n']
        self.assertEqual(self._run(lines), [], "String-literal-only log should not be flagged")

    def test_error_with_safe_second_arg(self):
        lines = ['  console.warn("[waitlist] owner notify failed", sent.error);\n']
        self.assertEqual(self._run(lines), [], "Non-sensitive second arg should not be flagged")

    def test_refreshtoken_flagged(self):
        lines = ["  logger.info('Refresh done', refreshToken);\n"]
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0)

    def test_commented_line_skipped(self):
        lines = ["  // console.log('Token:', accessToken);\n"]
        self.assertEqual(self._run(lines), [])

    def test_nosec_suppresses(self):
        lines = ["  console.log('Token:', accessToken);  // nosec: intentional for dev debugging\n"]
        self.assertEqual(self._run(lines), [])


class TestCheckC(unittest.TestCase):
    def _run(self, lines):
        return security_scan.check_C_injection(Path("fake.ts"), lines)

    def test_sql_template_literal(self):
        lines = ['  const r = await db.query(`SELECT * FROM users WHERE id = ${userId}`);\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0)
        self.assertIn("A03", findings[0]["owasp_ids"])

    def test_parameterized_sql_not_flagged(self):
        lines = ['  await db.prepare("SELECT * FROM users WHERE id = ?").bind(userId).run();\n']
        self.assertEqual(self._run(lines), [])

    def test_eval_flagged(self):
        lines = ["  eval(userInput);\n"]
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0)

    def test_shell_flagged(self):
        lines = ['  child_process.exec("ls " + userInput);\n']
        findings = self._run(lines)
        self.assertTrue(len(findings) > 0)


class TestCheckE(unittest.TestCase):
    def _run(self, path_str, lines):
        return security_scan.check_E_rate_limiting(Path(path_str), lines)

    def test_api_post_with_mutation_no_ratelimit(self):
        lines = [
            "export const POST = async ({ request }) => {\n",
            "  await sendEmail(env, { to: 'a@b.com' });\n",
            "};\n",
        ]
        findings = self._run("src/pages/api/contact.ts", lines)
        self.assertTrue(len(findings) > 0)
        self.assertEqual(findings[0]["severity"], "MEDIUM")

    def test_api_with_ratelimit_not_flagged(self):
        lines = [
            "export const POST = async ({ request }) => {\n",
            "  await rateLimiter.limit(ip);\n",
            "  await sendEmail(env, { to: 'a@b.com' });\n",
            "};\n",
        ]
        self.assertEqual(self._run("src/pages/api/contact.ts", lines), [])

    def test_non_api_file_skipped(self):
        lines = [
            "export const POST = async () => {\n",
            "  await sendEmail(env, {});\n",
            "};\n",
        ]
        self.assertEqual(self._run("src/lib/helper.ts", lines), [])


class TestCheckGStaticPromptRHS(unittest.TestCase):
    """`prompt +=` with a provably static RHS must not flag; dynamic RHS must."""

    def _g(self, lines, name="src/Coach.swift"):
        return security_scan.check_G_prompt_injection(Path(name), lines)

    def test_static_single_line_literal_not_flagged(self):
        self.assertEqual(self._g(['prompt += "\\n\\n"\n']), [])

    def test_static_multiline_block_not_flagged(self):
        lines = [
            'prompt += """\n',
            "Return one JSON object only. No prose outside JSON.\n",
            '{ "summary": "1 short paragraph" }\n',
            '"""\n',
        ]
        self.assertEqual(self._g(lines), [])

    def test_interpolated_multiline_block_still_flagged(self):
        lines = [
            'prompt += """\n',
            "Topic: \\(topic)\n",
            '"""\n',
        ]
        self.assertEqual(len(self._g(lines)), 1)

    def test_bare_identifier_still_flagged(self):
        self.assertEqual(len(self._g(["prompt += userInput\n"])), 1)

    def test_wrapped_call_still_flagged(self):
        # Sanitizer calls are judged at the call site via nosec, not trusted by name.
        self.assertEqual(
            len(self._g(["prompt += PromptBoundary.turnBlock(role, content)\n"])), 1
        )

    def test_js_template_interpolation_still_flagged(self):
        self.assertEqual(len(self._g(["prompt += `hi ${name}`\n"], "src/x.ts")), 1)

    def test_unterminated_multiline_stays_flagged(self):
        self.assertEqual(len(self._g(['prompt += """\n', "static text\n"])), 1)


class TestCleanFile(unittest.TestCase):
    def test_no_findings_on_clean_code(self):
        lines = [
            'const name = "Alice";\n',
            'console.log("Hello", name);\n',
            'const url = "https://example.com";\n',
            "const id = userId;\n",
            'const msg = `Hello ${name}`;\n',
        ]
        path = Path("src/lib/clean.ts")
        all_f: list = []
        all_f.extend(security_scan.check_A_secrets(path, lines, None))
        all_f.extend(security_scan.check_B_secret_in_logs(path, lines))
        all_f.extend(security_scan.check_C_injection(path, lines))
        all_f.extend(security_scan.check_D_ssrf(path, lines))
        all_f.extend(security_scan.check_G_prompt_injection(path, lines))
        self.assertEqual(all_f, [], f"Unexpected findings: {all_f}")


class _DiffScanBase(unittest.TestCase):
    """Shared helpers for the --diff / --exclude integration tests.

    These drive the real CLI (subprocess) so exit codes and arg parsing are
    exercised end-to-end, and build throwaway git repos under the OS temp dir
    (never committed to build-loop). The fake token is synthetic.
    """

    def _mkdir(self):
        d = Path(tempfile.mkdtemp(prefix="secscan_"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(d)]))
        return d

    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=str(cwd), capture_output=True, text=True,
        )

    def _init(self, cwd):
        self._git(cwd, "init", "-q")

    def _write(self, cwd, rel, content):
        p = cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _commit(self, cwd, msg="c"):
        self._git(cwd, "add", "-A")
        self._git(cwd, "commit", "-q", "-m", msg)
        return self._git(cwd, "rev-parse", "HEAD").stdout.strip()

    def _scan(self, cwd, *extra):
        """Run the scanner CLI in --json mode. Returns (rc, parsed_dict)."""
        proc = subprocess.run(
            [sys.executable, str(_SCANNER), "--path", str(cwd), "--json", *extra],
            capture_output=True, text=True,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
        return proc.returncode, data

    def _has_secret(self, data, name_endswith):
        return any(
            f["severity"] == "HIGH" and f["check_id"] == "A"
            and f["file"].endswith(name_endswith)
            for f in (data or {}).get("findings", [])
        )


class TestDiffIntentPreserved(_DiffScanBase):
    """The 2026-06 catch must survive delta mode: a planted secret in a file
    that IS in the <ref>..HEAD range still produces HIGH + rc==1."""

    def test_secret_in_diff_range_still_high(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        base = self._commit(d, "baseline")
        self._write(d, "src/auth.ts", _SECRET_LINE)
        self._commit(d, "add auth")
        rc, data = self._scan(d, "--diff", base)
        self.assertEqual(rc, 1, "planted secret in diff range must fail the gate")
        self.assertTrue(self._has_secret(data, "src/auth.ts"),
                        "secret in changed file must still be reported in delta mode")


class TestDiffMisScopeFixed(_DiffScanBase):
    """A HIGH finding in a file NOT in the range is NOT reported (the
    unrelated-debt case). A full scan of the same repo DOES report it."""

    def test_out_of_range_debt_not_reported(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/debt.ts", _SECRET_LINE)  # pre-existing debt
        base = self._commit(d, "baseline with debt")
        self._write(d, "docs/notes.md", "# just a note\n")
        self._commit(d, "add doc")
        # Delta mode: debt.ts is not in base..HEAD → not reported.
        rc, data = self._scan(d, "--diff", base)
        self.assertEqual(rc, 0, "unrelated pre-existing debt must not block the push")
        self.assertFalse(self._has_secret(data, "src/debt.ts"),
                         "out-of-range debt must not be reported in delta mode")
        # Full scan (default) still surfaces it — proves scoping, not deletion.
        rc_full, data_full = self._scan(d)
        self.assertEqual(rc_full, 1)
        self.assertTrue(self._has_secret(data_full, "src/debt.ts"))


class TestExcludeHonored(_DiffScanBase):
    """--exclude '<glob>' suppresses a finding in a matching path."""

    def test_exclude_suppresses_matching_path(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "research/vendor.ts", _SECRET_LINE)
        self._commit(d, "add vendor")
        rc, data = self._scan(d)  # no exclude → found
        self.assertEqual(rc, 1)
        self.assertTrue(self._has_secret(data, "research/vendor.ts"))
        rc_x, data_x = self._scan(d, "--exclude", "research/*")  # excluded
        self.assertEqual(rc_x, 0, "excluded path must not block the push")
        self.assertFalse(self._has_secret(data_x, "research/vendor.ts"))


class TestBackCompatDefault(_DiffScanBase):
    """With neither flag, whole-repo behavior + output shape are unchanged."""

    def test_default_whole_repo_scan_unchanged(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/auth.ts", _SECRET_LINE)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        rc, data = self._scan(d)
        self.assertEqual(rc, 1, "default whole-repo scan must still catch the secret")
        self.assertTrue(self._has_secret(data, "src/auth.ts"))
        self.assertNotIn("diff", data, "default output must not carry a diff key")


class TestFallbackFailSafe(_DiffScanBase):
    """--diff <bad-ref> and a non-git path both fall back to a full scan
    (never silently pass)."""

    def test_bad_ref_falls_back_to_full_scan(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/auth.ts", _SECRET_LINE)
        self._commit(d, "baseline")
        rc, data = self._scan(d, "--diff", "no-such-ref-xyz")
        self.assertEqual(rc, 1, "bad ref must fall back to full scan, not silently pass")
        self.assertTrue(self._has_secret(data, "src/auth.ts"))
        self.assertEqual(data.get("diff", {}).get("mode"), "fallback-full-scan")

    def test_non_git_path_falls_back_to_full_scan(self):
        d = self._mkdir()  # no git init
        self._write(d, "src/auth.ts", _SECRET_LINE)
        rc, data = self._scan(d, "--diff", "HEAD")
        self.assertEqual(rc, 1, "non-git path must fall back to full scan")
        self.assertTrue(self._has_secret(data, "src/auth.ts"))
        self.assertEqual(data.get("diff", {}).get("mode"), "fallback-full-scan")


class TestEmptyDiff(_DiffScanBase):
    """An empty diff (nothing changed) scans nothing and exits 0."""

    def test_empty_diff_scans_nothing(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/auth.ts", _SECRET_LINE)
        self._commit(d, "baseline")
        rc, data = self._scan(d, "--diff", "HEAD")  # HEAD..HEAD is empty
        self.assertEqual(rc, 0, "empty diff must exit 0")
        self.assertFalse(self._has_secret(data, "src/auth.ts"))
        self.assertEqual(data.get("files_scanned"), 0)


class TestDiffQuotepathFilename(_DiffScanBase):
    """f1 (HIGH): a secret in a delta file whose name is non-ASCII must still be
    caught. git's default core.quotepath octal-escapes-and-quotes such names in
    plain `--name-only`; without `-z` the file is silently dropped from the
    delta (rc 0). RED on the pre-fix code (no `-z`, no belt-and-braces)."""

    def test_nonascii_filename_secret_in_delta_caught(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        self._write(d, "src/café.py", _SECRET_LINE)  # café.py
        self._commit(d, "add non-ascii-named secret")
        rc, data = self._scan(d, "--diff", "HEAD~1")
        self.assertEqual(rc, 1, "secret in a non-ASCII-named delta file must fail the gate")
        self.assertTrue(self._has_secret(data, "café.py"),
                        "quotepath-mangled filename must not be dropped from the delta")


class TestDiffSubdirPath(_DiffScanBase):
    """f2 (HIGH): --path <subdir> --diff <ref> must scan the subdir's delta.
    Without `--relative`, git emits repo-root-relative diff paths that the
    subdir-rooted join mangles → whole delta dropped (rc 0). RED on pre-fix."""

    def test_secret_under_subdir_caught_with_subdir_path(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "pkg/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        self._write(d, "pkg/secret.ts", _SECRET_LINE)
        self._commit(d, "add secret under pkg/")
        rc, data = self._scan(d / "pkg", "--diff", "HEAD~1")
        self.assertEqual(rc, 1, "secret in the subdir delta must fail the gate")
        self.assertTrue(self._has_secret(data, "secret.ts"),
                        "subdir --path must not drop the whole delta")

    def test_subdir_full_scan_agrees(self):
        """Control: a full scan rooted at the subdir also finds it (proves the
        --diff result is scoping, not luck)."""
        d = self._mkdir()
        self._init(d)
        self._write(d, "pkg/secret.ts", _SECRET_LINE)
        self._commit(d, "add secret under pkg/")
        rc, data = self._scan(d / "pkg")  # full scan at subdir
        self.assertEqual(rc, 1)
        self.assertTrue(self._has_secret(data, "secret.ts"))


class TestExcludeVisibility(_DiffScanBase):
    """f4 (MED): an active --exclude must surface its globs + the count of files
    it removed, so an over-broad glob cannot silently bypass the scan."""

    def _scan_human(self, cwd, *extra):
        proc = subprocess.run(
            [sys.executable, str(_SCANNER), "--path", str(cwd), *extra],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_report_names_globs_and_removed_count(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "research/vendor.ts", _SECRET_LINE)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        rc, out, _err = self._scan_human(d, "--exclude", "research/*")
        self.assertEqual(rc, 0, "excluded secret must not block")
        self.assertIn("research/*", out, "report must name the active exclude glob")
        self.assertIn("removed 1 file", out, "report must state how many files the glob removed")

    def test_json_carries_exclude_block(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "research/vendor.ts", _SECRET_LINE)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        rc, data = self._scan(d, "--exclude", "research/*")
        self.assertEqual(rc, 0)
        self.assertEqual(data.get("exclude", {}).get("globs"), ["research/*"])
        self.assertEqual(data.get("exclude", {}).get("files_removed"), 1)

    def test_bare_wildcard_warns_on_stderr(self):
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/auth.ts", _SECRET_LINE)
        self._commit(d, "baseline")
        rc, _out, err = self._scan_human(d, "--exclude", "*")
        self.assertEqual(rc, 0, "bare `*` bypasses everything (documented, but must warn)")
        self.assertIn("bypasses the entire scan", err,
                      "a bare `*` exclude must emit a stderr warning")

    def test_default_no_exclude_block(self):
        """Back-compat: no --exclude → no exclude key in JSON output."""
        d = self._mkdir()
        self._init(d)
        self._write(d, "src/clean.ts", 'const name = "Alice";\n')
        self._commit(d, "baseline")
        _rc, data = self._scan(d)
        self.assertNotIn("exclude", data, "default output must not carry an exclude key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
