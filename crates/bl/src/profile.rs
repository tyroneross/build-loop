// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Use-case profiles for `bl run`: one small JSON file per use case.
//!
//! JSON, not TOML: `serde_json` is already a dependency and a TOML subset
//! parser would be new code to trust. The shape (`build-loop.profile.v1`):
//!
//! ```json
//! {
//!   "schema": "build-loop.profile.v1",
//!   "name": "quick",
//!   "agent": {
//!     "host": "claude_code",            // taxonomy host: which models it can run
//!     "command": "claude-agent-acp", "args": [], "env": {},
//!     "model_env": "ANTHROPIC_MODEL",   // optional: also hand the model over at spawn
//!     "mode": "default",                // optional: value for the session's mode option
//!     "session_meta": {                 // agent-specific `_meta` for session/new
//!       "claudeCode": {"options": {"settings": {"permissions": {"ask": ["Bash", "Read", "Edit", "Write"]}}}}
//!     }
//!   },
//!   "sign_in": {"mode": "subscription", "unset_env": []},
//!   "models": {"default": {"segment": "agentic_execution", "tier": "T3"}},
//!   "instructions": {"method": true, "append": ["..."]},
//!   "policy": "slice1-experimental",
//!   "mcp_servers": [], "skills": [],
//!   "deadline_seconds": 600
//! }
//! ```
//!
//! `sign_in.mode` is `subscription` in this slice: the agent uses the login
//! its own tooling already holds, and `bl` never reads a credential. In
//! that mode the provider key variables ([`SUBSCRIPTION_UNSET`]) are always
//! removed from the agent's environment, whatever the profile says, and a
//! profile may not set an environment name that looks like a credential. The
//! values `api-key` and `local` are reserved for the same field and refused
//! until they are built. `models` maps a kind of work to a `(segment, tier)`
//! role that `bl-route` resolves.
//!
//! A profile names the command `bl` starts, so where it comes from matters:
//! a bare name resolves only to `<plugin root>/profiles/<name>.json`. A
//! project's own profile must be given as an explicit path.
//!
//! `bl run` refuses a profile whose session would let tools run without a
//! permission request (no `ask` rules, or a mode other than `default` or
//! `plan`) unless `--allow-unasked` is passed; see [`Profile::unasked_risk`].

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub const SCHEMA: &str = "build-loop.profile.v1";
const DEFAULT_DEADLINE_SECONDS: u64 = 600;
/// One day. Also keeps the deadline arithmetic far from overflow.
const MAX_DEADLINE_SECONDS: u64 = 86_400;
/// Removed from the agent's environment in `subscription` mode, always: a
/// provider key there would be used instead of the stored login.
pub const SUBSCRIPTION_UNSET: [&str; 6] = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CURSOR_API_KEY"];
/// A profile may not set an environment name containing one of these.
const CREDENTIAL_WORDS: [&str; 5] = ["key", "token", "secret", "password", "auth"];
/// Session modes in which a tool call needing approval asks first.
const ASKING_MODES: [&str; 2] = ["default", "plan"];

fn credential_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    CREDENTIAL_WORDS.iter().any(|w| lower.contains(w))
}

#[derive(Debug, Clone, PartialEq)]
pub struct ModelRule {
    pub segment: String,
    pub tier: String,
}

#[derive(Debug, Clone)]
pub struct Profile {
    pub name: String,
    pub path: PathBuf,
    pub host: String,
    pub command: String,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub model_env: Option<String>,
    pub mode: Option<String>,
    pub session_meta: Value,
    pub sign_in: String,
    pub unset_env: Vec<String>,
    pub models: Vec<(String, ModelRule)>,
    pub method: bool,
    pub append: Vec<String>,
    pub policy: String,
    pub mcp_servers: Value,
    pub skills: Vec<String>,
    pub deadline_seconds: u64,
}

fn text(obj: &Map<String, Value>, key: &str, at: &str) -> Result<String, String> {
    match obj.get(key) {
        Some(Value::String(s)) if !s.trim().is_empty() => Ok(s.clone()),
        Some(_) => Err(format!("{at}.{key} must be a non-empty string")),
        None => Err(format!("{at}.{key} is missing")),
    }
}

fn optional_text(obj: &Map<String, Value>, key: &str, at: &str) -> Result<Option<String>, String> {
    match obj.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(_) => text(obj, key, at).map(Some),
    }
}

fn strings(obj: &Map<String, Value>, key: &str, at: &str) -> Result<Vec<String>, String> {
    match obj.get(key) {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(items)) => items.iter().map(|v| v.as_str().map(str::to_string).ok_or_else(|| format!("{at}.{key} must be a list of strings"))).collect(),
        Some(_) => Err(format!("{at}.{key} must be a list of strings")),
    }
}

fn object<'a>(obj: &'a Map<String, Value>, key: &str) -> Result<&'a Map<String, Value>, String> {
    obj.get(key).and_then(Value::as_object).ok_or_else(|| format!("{key} must be an object"))
}

impl Profile {
    pub fn parse(source: &str, path: &Path) -> Result<Profile, String> {
        let root: Value = serde_json::from_str(source).map_err(|e| format!("not JSON: {e}"))?;
        let root = root.as_object().ok_or("the profile must be a JSON object")?;
        if root.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
            return Err(format!("schema must be {SCHEMA:?}"));
        }
        let agent = object(root, "agent")?;
        let sign_in = object(root, "sign_in")?;
        let mode = text(sign_in, "mode", "sign_in")?;
        match mode.as_str() {
            "subscription" => {}
            "api-key" | "local" => return Err(format!("sign_in.mode {mode:?} is reserved and not built in this slice; use \"subscription\"")),
            other => return Err(format!("sign_in.mode {other:?} is unknown (subscription; reserved: api-key, local)")),
        }
        let mut models = Vec::new();
        for (kind, rule) in object(root, "models")? {
            let rule = rule.as_object().ok_or_else(|| format!("models.{kind} must be an object"))?;
            let at = format!("models.{kind}");
            models.push((kind.clone(), ModelRule { segment: text(rule, "segment", &at)?, tier: text(rule, "tier", &at)? }));
        }
        if models.is_empty() {
            return Err("models needs at least one kind of work".into());
        }
        let empty = Map::new();
        let instructions = match root.get("instructions") {
            None | Some(Value::Null) => &empty,
            Some(Value::Object(o)) => o,
            Some(_) => return Err("instructions must be an object".into()),
        };
        let env = match agent.get("env") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Object(o)) => o.iter().map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_string())).ok_or_else(|| format!("agent.env.{k} must be a string"))).collect::<Result<_, _>>()?,
            Some(_) => return Err("agent.env must be an object".into()),
        };
        let model_env = optional_text(agent, "model_env", "agent")?;
        // Names only: a value is never read into an error or a log line.
        if let Some(name) = env.iter().map(|(k, _)| k).chain(model_env.iter()).find(|k| credential_name(k)) {
            return Err(format!("agent environment name {name:?} looks like a credential; a profile may not carry one"));
        }
        let mut unset_env: Vec<String> = SUBSCRIPTION_UNSET.iter().map(|s| s.to_string()).collect();
        for name in strings(sign_in, "unset_env", "sign_in")? {
            if !unset_env.contains(&name) {
                unset_env.push(name);
            }
        }
        let mcp_servers = match root.get("mcp_servers") {
            None | Some(Value::Null) => Value::Array(Vec::new()),
            Some(v @ Value::Array(_)) => v.clone(),
            Some(_) => return Err("mcp_servers must be a list".into()),
        };
        Ok(Profile {
            name: text(root, "name", "profile")?,
            path: path.to_path_buf(),
            host: text(agent, "host", "agent")?,
            command: text(agent, "command", "agent")?,
            args: strings(agent, "args", "agent")?,
            env,
            model_env,
            mode: optional_text(agent, "mode", "agent")?,
            session_meta: agent.get("session_meta").cloned().unwrap_or(Value::Null),
            sign_in: mode,
            unset_env,
            models,
            method: instructions.get("method").and_then(Value::as_bool).unwrap_or(true),
            append: strings(instructions, "append", "instructions")?,
            policy: text(root, "policy", "profile")?,
            mcp_servers,
            skills: strings(root, "skills", "profile")?,
            deadline_seconds: match root.get("deadline_seconds") {
                None | Some(Value::Null) => DEFAULT_DEADLINE_SECONDS,
                Some(v) => v.as_u64().filter(|s| (1..=MAX_DEADLINE_SECONDS).contains(s)).ok_or_else(|| format!("deadline_seconds must be a whole number from 1 to {MAX_DEADLINE_SECONDS}"))?,
            },
        })
    }

    /// An explicit path (contains `/` or ends in `.json`), else a bare name,
    /// which resolves only to `<root>/profiles/<name>.json`: a profile
    /// names the command to start, so a file inside the project being
    /// worked on is never picked up by name.
    pub fn load(name_or_path: &str, root: Option<&Path>) -> Result<Profile, String> {
        let path = if name_or_path.contains('/') || name_or_path.ends_with(".json") {
            PathBuf::from(name_or_path)
        } else {
            root.ok_or("no build-loop checkout around the binary; give the profile as a path")?.join("profiles").join(format!("{name_or_path}.json"))
        };
        match std::fs::read_to_string(&path) {
            Ok(source) => Profile::parse(&source, &path).map_err(|e| format!("profile {}: {e}", path.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Err(format!("profile {name_or_path:?} not found at {} (a project's own profile must be given as a path)", path.display())),
            Err(e) => Err(format!("profile {}: {e}", path.display())),
        }
    }

    /// Why this profile's session could run tools without asking, if it
    /// could: no `ask` rules in the session `_meta`
    /// (`claudeCode.options.settings.permissions.ask`, the one agent this
    /// slice drives), or a mode that does not ask.
    pub fn unasked_risk(&self) -> Option<String> {
        let rules = self.session_meta.pointer("/claudeCode/options/settings/permissions/ask").and_then(Value::as_array).map(Vec::len).unwrap_or(0);
        if rules == 0 {
            return Some("the profile sets no ask rules, so tools the agent approves on its own run without a permission request".into());
        }
        match self.mode.as_deref() {
            Some(mode) if ASKING_MODES.contains(&mode) => None,
            Some(mode) => Some(format!("the profile sets mode {mode:?}, which lets tools run without a permission request")),
            None => Some("the profile sets no mode, so the session keeps whatever mode the agent starts in".into()),
        }
    }

    pub fn model_rule(&self, work: &str) -> Result<&ModelRule, String> {
        self.models.iter().find(|(kind, _)| kind == work).map(|(_, rule)| rule).ok_or_else(|| format!("profile {:?} has no model rule for work {work:?} (has: {})", self.name, self.models.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>().join(", ")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(json: Value) -> Result<Profile, String> {
        Profile::parse(&json.to_string(), Path::new("p.json"))
    }

    fn minimal() -> Value {
        serde_json::json!({
            "schema": SCHEMA, "name": "t",
            "agent": {"host": "claude_code", "command": "agent"},
            "sign_in": {"mode": "subscription"},
            "models": {"default": {"segment": "agentic_execution", "tier": "T3"}},
            "policy": "slice1-experimental"
        })
    }

    #[test]
    fn minimal_profile_gets_defaults() {
        let p = parse(minimal()).unwrap();
        assert_eq!((p.method, p.deadline_seconds, p.skills.len()), (true, DEFAULT_DEADLINE_SECONDS, 0));
        assert_eq!(p.mcp_servers, serde_json::json!([]));
        assert_eq!(p.model_rule("default").unwrap().tier, "T3");
        assert!(p.model_rule("review").unwrap_err().contains("has: default"));
    }

    #[test]
    fn reserved_sign_in_modes_are_named_and_refused() {
        for mode in ["api-key", "local"] {
            let mut v = minimal();
            v["sign_in"]["mode"] = mode.into();
            assert!(parse(v).unwrap_err().contains("reserved"));
        }
        let mut v = minimal();
        v["sign_in"]["mode"] = "oauth".into();
        assert!(parse(v).unwrap_err().contains("unknown"));
    }

    #[test]
    fn malformed_profiles_say_which_field() {
        let mut v = minimal();
        v["schema"] = "x".into();
        assert!(parse(v).unwrap_err().contains("schema"));
        let mut v = minimal();
        v["models"]["default"] = serde_json::json!({"segment": "s"});
        assert_eq!(parse(v).unwrap_err(), "models.default.tier is missing");
        let mut v = minimal();
        v["agent"]["env"] = serde_json::json!({"A": 1});
        assert_eq!(parse(v).unwrap_err(), "agent.env.A must be a string");
        let mut v = minimal();
        v["deadline_seconds"] = 0.into();
        assert!(parse(v).unwrap_err().contains("deadline_seconds"));
        let mut v = minimal();
        v["deadline_seconds"] = u64::MAX.into();
        assert!(parse(v).unwrap_err().contains("deadline_seconds"));
    }

    /// Finding 2: a bare name never resolves inside the project.
    #[test]
    fn a_bare_name_resolves_only_under_the_plugin_root() {
        let base = std::env::temp_dir().join(format!("bl-profile-{}", std::process::id()));
        let (root, project) = (base.join("root"), base.join("project"));
        std::fs::create_dir_all(root.join("profiles")).unwrap();
        std::fs::create_dir_all(project.join(".build-loop/profiles")).unwrap();
        let mut planted = minimal();
        planted["agent"]["command"] = "/tmp/evil".into();
        std::fs::write(project.join(".build-loop/profiles/quick.json"), planted.to_string()).unwrap();
        std::fs::write(root.join("profiles/quick.json"), minimal().to_string()).unwrap();
        let by_name = Profile::load("quick", Some(&root));
        let missing = Profile::load("other", Some(&root));
        let by_path = Profile::load(project.join(".build-loop/profiles/quick.json").to_str().unwrap(), Some(&root));
        let by_name = by_name.unwrap();
        assert_eq!((by_name.command.as_str(), by_name.path.clone()), ("agent", root.join("profiles/quick.json")));
        assert!(missing.unwrap_err().contains("must be given as a path"));
        assert_eq!(by_path.unwrap().command, "/tmp/evil");
        assert!(Profile::load("quick", None).is_err());
        std::fs::remove_dir_all(base).unwrap();
    }

    /// Finding 5: subscription sign-in is enforced, not requested.
    #[test]
    fn subscription_mode_always_unsets_provider_keys_and_refuses_credential_names() {
        let p = parse(minimal()).unwrap();
        for name in SUBSCRIPTION_UNSET {
            assert!(p.unset_env.iter().any(|n| n == name), "{name}");
        }
        let mut v = minimal();
        v["sign_in"]["unset_env"] = serde_json::json!(["EXTRA"]);
        assert!(parse(v).unwrap().unset_env.contains(&"EXTRA".to_string()));
        for name in ["ANTHROPIC_API_KEY", "My_Token", "DB_PASSWORD", "client_secret", "X_AUTH"] {
            let mut v = minimal();
            v["agent"]["env"] = serde_json::json!({name: "do-not-print-this-value"});
            let err = parse(v).unwrap_err();
            assert!(err.contains(name) && err.contains("credential") && !err.contains("do-not-print"), "{err}");
        }
        let mut v = minimal();
        v["agent"]["model_env"] = "MODEL_TOKEN".into();
        assert!(parse(v).unwrap_err().contains("credential"));
        let mut v = minimal();
        v["agent"]["env"] = serde_json::json!({"NODE_OPTIONS": "x"});
        assert!(parse(v).is_ok());
    }

    /// Finding 6: a profile that would not ask says so.
    #[test]
    fn a_profile_without_ask_rules_or_with_a_non_asking_mode_is_flagged() {
        let ask = serde_json::json!({"claudeCode": {"options": {"settings": {"permissions": {"ask": ["Bash"]}}}}});
        let with = |meta: Value, mode: Option<&str>| {
            let mut v = minimal();
            v["agent"]["session_meta"] = meta;
            if let Some(mode) = mode {
                v["agent"]["mode"] = mode.into();
            }
            parse(v).unwrap().unasked_risk()
        };
        assert_eq!(with(ask.clone(), Some("default")), None);
        assert_eq!(with(ask.clone(), Some("plan")), None);
        assert!(with(Value::Null, Some("default")).unwrap().contains("no ask rules"));
        assert!(with(serde_json::json!({"claudeCode": {"options": {"settings": {"permissions": {"ask": []}}}}}), Some("default")).unwrap().contains("no ask rules"));
        for mode in ["bypassPermissions", "acceptEdits", "auto", "dontAsk"] {
            assert!(with(ask.clone(), Some(mode)).unwrap().contains(mode));
        }
        assert!(with(ask, None).unwrap().contains("no mode"));
    }

    #[test]
    fn the_shipped_profiles_parse() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../profiles");
        let mut seen = 0;
        for entry in std::fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().is_some_and(|e| e == "json") {
                let p = Profile::load(path.to_str().unwrap(), None).unwrap();
                assert_eq!(p.unasked_risk(), None);
                assert_eq!(Some(p.name.as_str()), path.file_stem().and_then(|s| s.to_str()));
                assert!(bl_gate::by_name(&p.policy).is_some());
                seen += 1;
            }
        }
        assert!(seen >= 2);
    }
}
