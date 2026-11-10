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
//!     "session_meta": {}                // agent-specific `_meta` for session/new
//!   },
//!   "sign_in": {"mode": "subscription", "unset_env": ["ANTHROPIC_API_KEY"]},
//!   "models": {"default": {"segment": "agentic_execution", "tier": "T3"}},
//!   "instructions": {"method": true, "append": ["..."]},
//!   "policy": "slice1-experimental",
//!   "mcp_servers": [], "skills": [],
//!   "deadline_seconds": 600
//! }
//! ```
//!
//! `sign_in.mode` is `subscription` in this slice: the agent uses the login
//! its own tooling already holds, and `bl` never reads a credential. The
//! values `api-key` and `local` are reserved for the same field and refused
//! until they are built. `models` maps a kind of work to a `(segment, tier)`
//! role that `bl-route` resolves.

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub const SCHEMA: &str = "build-loop.profile.v1";
const DEFAULT_DEADLINE_SECONDS: u64 = 600;

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
            model_env: optional_text(agent, "model_env", "agent")?,
            mode: optional_text(agent, "mode", "agent")?,
            session_meta: agent.get("session_meta").cloned().unwrap_or(Value::Null),
            sign_in: mode,
            unset_env: strings(sign_in, "unset_env", "sign_in")?,
            models,
            method: instructions.get("method").and_then(Value::as_bool).unwrap_or(true),
            append: strings(instructions, "append", "instructions")?,
            policy: text(root, "policy", "profile")?,
            mcp_servers,
            skills: strings(root, "skills", "profile")?,
            deadline_seconds: match root.get("deadline_seconds") {
                None | Some(Value::Null) => DEFAULT_DEADLINE_SECONDS,
                Some(v) => v.as_u64().filter(|s| *s > 0).ok_or("deadline_seconds must be a positive whole number")?,
            },
        })
    }

    /// A path (contains `/` or ends in `.json`), else a name looked up as
    /// `<project>/.build-loop/profiles/<name>.json`, then
    /// `<root>/profiles/<name>.json`.
    pub fn load(name_or_path: &str, project: &Path, root: Option<&Path>) -> Result<Profile, String> {
        let candidates: Vec<PathBuf> = if name_or_path.contains('/') || name_or_path.ends_with(".json") {
            vec![PathBuf::from(name_or_path)]
        } else {
            let file = format!("{name_or_path}.json");
            std::iter::once(project.join(".build-loop/profiles").join(&file)).chain(root.map(|r| r.join("profiles").join(&file))).collect()
        };
        for path in &candidates {
            match std::fs::read_to_string(path) {
                Ok(source) => return Profile::parse(&source, path).map_err(|e| format!("profile {}: {e}", path.display())),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                Err(e) => return Err(format!("profile {}: {e}", path.display())),
            }
        }
        Err(format!("profile {name_or_path:?} not found (looked in {})", candidates.iter().map(|p| p.display().to_string()).collect::<Vec<_>>().join(", ")))
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
    }

    #[test]
    fn the_shipped_profiles_parse() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../profiles");
        let mut seen = 0;
        for entry in std::fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().is_some_and(|e| e == "json") {
                let p = Profile::load(path.to_str().unwrap(), Path::new("."), None).unwrap();
                assert_eq!(Some(p.name.as_str()), path.file_stem().and_then(|s| s.to_str()));
                assert!(bl_gate::by_name(&p.policy).is_some());
                seen += 1;
            }
        }
        assert!(seen >= 2);
    }
}
