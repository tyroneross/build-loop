#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for security_checks_api.py (checks H-N). Stdlib unittest only.

Every check gets a matched pair: a vulnerable fixture that MUST fire and a
fixed fixture that MUST NOT. A check that only has a positive test proves it
matches something; the pair proves it discriminates.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import security_checks_api as api  # noqa: E402

API = Path("app/api/items/[id]/route.ts")
CLIENT = Path("src/components/Panel.tsx")


def L(src: str) -> list[str]:
    """Split a fixture into the line list the checks consume."""
    return [f"{line}\n" for line in src.strip("\n").split("\n")]


# ---------------------------------------------------------------------------
# H — object-level authorization
# ---------------------------------------------------------------------------

class TestH(unittest.TestCase):
    def _h(self, src, path=API):
        return api.check_H_object_authorization(path, L(src))

    def test_unscoped_read_fires(self):
        f = self._h("""
export async function GET(req, { params }) {
  const identity = await resolveIdentity();
  const doc = await db.document.findUnique({ where: { id: params.id } });
  return Response.json(doc);
}
""")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "HIGH")
        self.assertEqual(f[0]["owasp_ids"], "A01")

    def test_unscoped_mutation_is_critical(self):
        f = self._h("""
export async function DELETE(req, { params }) {
  const identity = await resolveIdentity();
  await db.document.delete({ where: { id: params.id } });
  return new Response(null, { status: 204 });
}
""")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "CRITICAL")

    def test_owner_predicate_clears(self):
        self.assertEqual(self._h("""
export async function GET(req, { params }) {
  const session = await auth();
  const doc = await db.document.findFirst({
    where: { id: params.id, userId: session.user.id },
  });
  return Response.json(doc);
}
"""), [])

    def test_tenant_predicate_clears(self):
        self.assertEqual(self._h("""
export async function GET(req, { params }) {
  const doc = await db.document.findFirst({
    where: { id: params.id, tenant_id: ctx.tenantId },
  });
}
"""), [])

    def test_no_request_input_clears(self):
        """A hardcoded id is not attacker-controlled."""
        self.assertEqual(self._h("""
export async function GET() {
  const doc = await db.document.findUnique({ where: { id: "singleton" } });
}
"""), [])

    def test_non_api_path_skipped(self):
        self.assertEqual(self._h("""
export async function GET(req, { params }) {
  const doc = await db.document.findUnique({ where: { id: params.id } });
}
""", Path("src/lib/helpers.ts")), [])

    def test_nosec_suppresses(self):
        self.assertEqual(self._h("""
export async function GET(req, { params }) {
  const doc = await db.document.findUnique({ where: { id: params.id } }); // nosec: public catalog
}
"""), [])


# ---------------------------------------------------------------------------
# I — missing / fail-open auth guard
# ---------------------------------------------------------------------------

class TestI(unittest.TestCase):
    def _i(self, src, path=API):
        return api.check_I_auth_guard(path, L(src))

    def test_no_auth_reference_fires(self):
        f = self._i("""
export async function POST(req) {
  const body = await req.json();
  await db.item.create({ data: body });
}
""")
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_auth_reference_clears_i1(self):
        f = self._i("""
export async function POST(req) {
  const session = await getServerSession();
  if (!session) return new Response("no", { status: 401 });
  await db.item.create({ data: { ownerId: session.user.id } });
}
""")
        self.assertEqual([x for x in f if x["severity"] == "HIGH"], [])

    def test_fail_open_env_comparison_is_critical(self):
        """The bypass: if CRON_SECRET is unset, undefined !== undefined is false."""
        f = self._i("""
export async function POST(req) {
  const token = req.headers.get("x-token");
  if (token !== process.env.CRON_SECRET) {
    return new Response("nope", { status: 401 });
  }
  await db.job.create({ data: {} });
}
""")
        crit = [x for x in f if x["severity"] == "CRITICAL"]
        self.assertEqual(len(crit), 1)
        self.assertIn("environment variable", crit[0]["message"])

    def test_env_presence_assertion_clears_fail_open(self):
        f = self._i("""
if (!process.env.CRON_SECRET) throw new Error("CRON_SECRET is required");
export async function POST(req) {
  const token = req.headers.get("x-token");
  if (token !== process.env.CRON_SECRET) {
    return new Response("nope", { status: 401 });
  }
  await db.job.create({ data: {} });
}
""")
        self.assertEqual([x for x in f if x["severity"] == "CRITICAL"], [])

    def test_non_mutating_route_skipped(self):
        self.assertEqual(self._i("""
export async function GET(req) {
  return Response.json({ ok: true });
}
"""), [])

    def test_python_environ_indexing_is_fail_closed(self):
        """os.environ["X"] raises on missing, so it does not fail open."""
        f = self._i("""
SECRET = os.environ["CRON_SECRET"]

@app.post("/run")
def run(request):
    if request.headers.get("x-token") != os.environ.get("CRON_SECRET"):
        return 401
    db.job.create()
""", Path("api/jobs.py"))
        self.assertEqual([x for x in f if x["severity"] == "CRITICAL"], [])


# ---------------------------------------------------------------------------
# J — privileged secret in the client bundle
# ---------------------------------------------------------------------------

class TestJ(unittest.TestCase):
    def _j(self, src, path=CLIENT):
        return api.check_J_client_exposed_secret(path, L(src))

    def test_service_role_is_critical(self):
        f = self._j('const admin = createClient(url, process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY);')
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "CRITICAL")

    def test_provider_key_is_high(self):
        f = self._j('const key = import.meta.env.VITE_OPENAI_API_KEY;')
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "HIGH")

    def test_stripe_secret_is_critical(self):
        f = self._j('const k = process.env.NEXT_PUBLIC_STRIPE_SECRET_KEY;')
        self.assertEqual(f[0]["severity"], "CRITICAL")

    def test_publishable_key_clears(self):
        self.assertEqual(self._j('const pk = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;'), [])

    def test_anon_key_clears(self):
        self.assertEqual(self._j('const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;'), [])

    def test_benign_public_var_clears(self):
        self.assertEqual(self._j('const url = process.env.NEXT_PUBLIC_SITE_URL;'), [])

    def test_expo_public_secret_fires(self):
        f = self._j('const k = process.env.EXPO_PUBLIC_ANTHROPIC_API_KEY;')
        self.assertEqual(f[0]["severity"], "HIGH")

    def test_env_file_declaration_fires(self):
        f = self._j("NEXT_PUBLIC_RESEND_API_KEY=re_live_abc123", Path(".env.production"))
        self.assertEqual(len(f), 1)


# ---------------------------------------------------------------------------
# K — session and token hygiene
# ---------------------------------------------------------------------------

class TestK(unittest.TestCase):
    def _k(self, src, path=API):
        return api.check_K_token_hygiene(path, L(src))

    def test_alg_none_is_critical(self):
        f = self._k("const payload = jwt.verify(token, key, { algorithms: ['none'] });")
        self.assertEqual(f[0]["severity"], "CRITICAL")

    def test_decode_without_verify_fires(self):
        f = self._k("const claims = jwt.decode(token);\nconst uid = claims.sub;")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "HIGH")

    def test_decode_then_verify_clears(self):
        self.assertEqual(self._k("""
const claims = jwt.decode(token);
const verified = jwt.verify(token, publicKey, { algorithms: ["RS256"] });
"""), [])

    def test_python_verify_disabled_fires(self):
        f = self._k('claims = jwt.decode(tok, options={"verify_signature": False})',
                    Path("api/auth.py"))
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_cookie_without_httponly_is_high(self):
        f = self._k("""
cookies().set("session", token, { secure: true, sameSite: "lax" });
""")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "HIGH")
        self.assertIn("httpOnly", f[0]["message"])

    def test_cookie_missing_samesite_only_is_medium(self):
        f = self._k("""
cookies().set("session", token, { httpOnly: true, secure: true });
""")
        self.assertEqual(f[0]["severity"], "MEDIUM")

    def test_fully_flagged_cookie_clears(self):
        self.assertEqual(self._k("""
cookies().set("session", token, {
  httpOnly: true,
  secure: true,
  sameSite: "strict",
});
"""), [])

    def test_non_session_cookie_clears(self):
        self.assertEqual(self._k('cookies().set("theme", "dark", {});'), [])

    def test_token_in_localstorage_fires(self):
        f = self._k("localStorage.setItem('access_token', t);", CLIENT)
        self.assertEqual(f[0]["severity"], "MEDIUM")

    def test_benign_localstorage_clears(self):
        self.assertEqual(self._k("localStorage.setItem('sidebar_width', w);", CLIENT), [])


# ---------------------------------------------------------------------------
# L — CORS
# ---------------------------------------------------------------------------

class TestL(unittest.TestCase):
    def _l(self, src, path=API):
        return api.check_L_cors(path, L(src))

    def test_wildcard_alone_is_medium(self):
        f = self._l('headers.set("Access-Control-Allow-Origin", "*");')
        self.assertEqual(f[0]["severity"], "MEDIUM")

    def test_wildcard_with_credentials_is_high(self):
        f = self._l("""
headers.set("Access-Control-Allow-Origin", "*");
headers.set("Access-Control-Allow-Credentials", "true");
""")
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_cors_middleware_origin_true_with_credentials(self):
        f = self._l("app.use(cors({ origin: true, credentials: true }));")
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_explicit_allowlist_clears(self):
        self.assertEqual(self._l("""
const ALLOWED = ["https://app.example.com"];
if (ALLOWED.includes(origin)) {
  headers.set("Access-Control-Allow-Origin", origin);
}
headers.set("Access-Control-Allow-Credentials", "true");
"""), [])


# ---------------------------------------------------------------------------
# M — mass assignment / unvalidated body
# ---------------------------------------------------------------------------

class TestM(unittest.TestCase):
    def _m(self, src, path=API):
        return api.check_M_mass_assignment(path, L(src))

    def test_body_spread_into_update_is_high(self):
        f = self._m("""
export async function PATCH(req) {
  const body = await req.json();
  await db.user.update({ where: { id: body.id }, data: { ...body } });
}
""")
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_zod_validation_clears(self):
        f = self._m("""
const Schema = z.object({ name: z.string().max(80) });
export async function PATCH(req) {
  const data = Schema.parse(await req.json());
  await db.user.update({ where: { id: session.user.id }, data });
}
""")
        self.assertEqual(f, [])

    def test_unvalidated_body_read_is_medium(self):
        f = self._m("""
export async function POST(req) {
  const body = await req.json();
  await db.item.create({ data: { name: body.name } });
}
""")
        self.assertTrue(any(x["severity"] == "MEDIUM" for x in f))

    def test_pydantic_clears(self):
        self.assertEqual(self._m("""
class ItemIn(BaseModel):
    name: str

@app.post("/items")
def create(item: ItemIn):
    db.item.create(name=item.name)
""", Path("api/items.py")), [])

    def test_non_mutating_route_skipped(self):
        self.assertEqual(self._m("""
export async function GET(req) {
  const body = await req.json();
}
"""), [])


# ---------------------------------------------------------------------------
# N — AI / agent boundary
# ---------------------------------------------------------------------------

class TestN(unittest.TestCase):
    def _n(self, src, path=Path("src/agent/run.ts")):
        return api.check_N_ai_boundary(path, L(src))

    def test_uncapped_model_call_is_medium(self):
        f = self._n("""
const res = await openai.chat.completions.create({
  model: "gpt-4o",
  messages,
});
""")
        self.assertTrue(any(x["check_id"] == "N" and x["severity"] == "MEDIUM" for x in f))

    def test_uncapped_call_in_loop_without_timeout_is_high(self):
        f = self._n("""
for (const chunk of chunks) {
  const res = await anthropic.messages.create({ model: "claude", messages: chunk });
}
""")
        self.assertTrue(any(x["severity"] == "HIGH" for x in f))

    def test_token_cap_clears(self):
        f = self._n("""
const res = await anthropic.messages.create({
  model: "claude",
  max_tokens: 1024,
  messages,
});
""")
        self.assertEqual([x for x in f if "token cap" in x["message"]], [])

    def test_model_output_into_exec_is_high(self):
        f = self._n("""
const completion = await openai.chat.completions.create({ model: "x", max_tokens: 10, messages });
execSync(completion.choices[0].message.content);
""")
        self.assertTrue(any("interpreter" in x["message"] for x in f))

    def test_model_output_mention_in_comment_ignored(self):
        f = self._n("""
// never pass completion.choices[0].message.content to eval(
const res = await openai.chat.completions.create({ model: "x", max_tokens: 5, messages });
""")
        self.assertEqual([x for x in f if "interpreter" in x["message"]], [])

    def test_unfiltered_vector_search_is_high(self):
        f = self._n("""
const docs = await vectorStore.similaritySearch(query, 8);
""")
        self.assertTrue(any("retrieval" in x["message"] for x in f))

    def test_filtered_vector_search_clears(self):
        f = self._n("""
const docs = await vectorStore.similaritySearch(query, 8, { tenant_id: ctx.tenantId });
""")
        self.assertEqual([x for x in f if "retrieval" in x["message"]], [])

    def test_ungated_tool_dispatch_is_high(self):
        f = self._n("""
for (const call of message.tool_calls) {
  const result = await TOOLS[call.name](call.arguments);
}
""")
        self.assertTrue(any("authorization layer" in x["message"] for x in f))

    def test_gated_tool_dispatch_clears(self):
        f = self._n("""
for (const call of message.tool_calls) {
  authorizeToolCall(user, call);
  const result = await TOOLS[call.name](call.arguments);
}
""")
        self.assertEqual([x for x in f if "authorization layer" in x["message"]], [])

    def test_transcript_parser_is_not_a_tool_dispatch(self):
        """The observed false-positive class: code that READS tool_use records."""
        f = self._n('''
def count_tool_use(record):
    """Count tool_use blocks in an assistant turn."""
    return sum(1 for b in record["content"] if b.get("type") == "tool_use")
''', Path("scripts/parse.py"))
        self.assertEqual([x for x in f if "authorization layer" in x["message"]], [])

    def test_plain_registry_without_llm_context_clears(self):
        f = self._n("""
const result = handlers[event.type](event.payload);
""", Path("src/events.ts"))
        self.assertEqual([x for x in f if "authorization layer" in x["message"]], [])


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------

class TestRegistry(unittest.TestCase):
    def test_spot_subset_is_a_subset_of_all_checks(self):
        all_ids = {"H", "I", "J", "K", "L", "M", "N"}
        self.assertTrue(api.SPOT_CHECK_IDS <= all_ids)

    def test_run_file_checks_dispatches_every_check(self):
        self.assertEqual(len(api.FILE_CHECKS), 7)

    def test_every_finding_carries_a_mapped_risk_id(self):
        f = api.run_file_checks(API, L("""
export async function DELETE(req, { params }) {
  await db.document.delete({ where: { id: params.id } });
}
"""))
        self.assertTrue(f)
        for x in f:
            self.assertTrue(x["owasp_ids"], "every finding must cite at least one risk ID")
            self.assertIn(x["severity"], {"CRITICAL", "HIGH", "MEDIUM", "LOW"})


if __name__ == "__main__":
    unittest.main()
