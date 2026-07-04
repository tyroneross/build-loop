#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for security_scan.py. Stdlib unittest only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import security_scan  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
