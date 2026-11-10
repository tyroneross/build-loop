// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Native model resolver over `references/model-taxonomy.json`.
//!
//! Returns the model id `python3 scripts/model_index.py resolve` and
//! `scripts/resolve_agent_model.py` return, by mirroring their selection
//! path (`model_index.do_resolve` -> `model_resolver` -> `model_overrides`
//! -> `model_taxonomy`): tier tokens, the ordered preferred list per
//! `(segment, tier)`, `.build-loop/config.json` and `state.json` overrides,
//! recorded outages with their TTL, the verified tier cache, host
//! reachability, RAM fit, and the floor walk (frontier never below
//! thinking). `tests/driver/test_route_parity.py` holds the two equal.
//!
//! Three deliberate differences from the Python:
//! - read-only: an expired outage record is ignored, never pruned from
//!   `model-availability.json` (Python rewrites the file on read);
//! - metadata lookup is by exact id, then case-insensitive id or alias. The
//!   fused-slug and family inheritance in `model_taxonomy.model_meta` is not
//!   ported; selection never needs it while every id in `preferred` and
//!   `legacy_registry` is a key of `models` ([`Taxonomy::unknown_ids`]);
//! - only the model, its source and whether it is dispatchable are returned,
//!   none of the advisory envelope (effort, prompting profile, chain).

mod taxonomy;

pub use taxonomy::Taxonomy;

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const LEGACY_ORDER: [&str; 4] = ["frontier", "thinking", "code", "pattern"];
const LEGACY_SEGMENT: &str = "generative_reasoning";
/// `model_availability_store.DEFAULT_TTL_SECONDS`.
const DEFAULT_TTL_SECONDS: f64 = 1800.0;

/// The `--host` argument.
#[derive(Debug, Clone, PartialEq)]
pub enum Host {
    /// Config `hostProviders`, then the detected host, then no filter.
    Default,
    /// `any`: no provider filter.
    Any,
    Providers(BTreeSet<String>),
}

/// What the resolver reads from the process environment. Only names and
/// whether a value is non-empty are kept, never a value, apart from the two
/// build-loop settings.
#[derive(Debug, Clone, Default)]
pub struct EnvView {
    pub names: BTreeSet<String>,
    pub non_empty: BTreeSet<String>,
    /// `BUILD_LOOP_HOST_PROVIDERS`.
    pub host_providers: Option<String>,
    /// Physical RAM in GB; `None` when unknown (nothing is filtered).
    pub ram_gb: Option<f64>,
}

impl EnvView {
    pub fn from_process() -> EnvView {
        let mut view = EnvView::default();
        for (name, value) in std::env::vars_os() {
            let Some(name) = name.to_str().map(str::to_string) else { continue };
            if !value.is_empty() {
                view.non_empty.insert(name.clone());
            }
            view.names.insert(name);
        }
        view.host_providers = std::env::var("BUILD_LOOP_HOST_PROVIDERS").ok();
        view.ram_gb = match std::env::var("BUILD_LOOP_HOST_RAM_GB") {
            Ok(raw) if !raw.is_empty() => raw.trim().parse::<f64>().ok(),
            _ => physical_ram_gb(),
        };
        view
    }
}

#[cfg(target_os = "macos")]
fn physical_ram_gb() -> Option<f64> {
    extern "C" {
        fn sysctlbyname(name: *const std::os::raw::c_char, oldp: *mut std::ffi::c_void, oldlenp: *mut usize, newp: *mut std::ffi::c_void, newlen: usize) -> std::os::raw::c_int;
    }
    let mut bytes: u64 = 0;
    let mut len = std::mem::size_of::<u64>();
    // SAFETY: the name is NUL-terminated and `bytes`/`len` describe one u64.
    let rc = unsafe { sysctlbyname(c"hw.memsize".as_ptr(), (&mut bytes as *mut u64).cast(), &mut len, std::ptr::null_mut(), 0) };
    (rc == 0 && bytes > 0).then(|| bytes as f64 / (1024.0 * 1024.0 * 1024.0))
}

#[cfg(not(target_os = "macos"))]
fn physical_ram_gb() -> Option<f64> {
    let text = std::fs::read_to_string("/proc/meminfo").ok()?;
    let line = text.lines().find(|l| l.starts_with("MemTotal:"))?;
    let kb: f64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kb / (1024.0 * 1024.0))
}

#[derive(Debug, Clone, PartialEq)]
pub struct Resolution {
    /// The model id; `None` when nothing resolves.
    pub model: Option<String>,
    /// Python's `source` / `resolver_source`.
    pub source: String,
    /// The model is present and not declared unavailable or unreachable.
    pub resolved: bool,
}

struct Entry {
    id: String,
    provider: String,
    aliases: Vec<String>,
}

/// `model_overrides.MODEL_REGISTRY` and what is derived from it.
struct Registry {
    tiers: Vec<(&'static str, Vec<Entry>)>,
    alias_index: BTreeMap<String, String>,
    defaults: BTreeMap<&'static str, String>,
    fallback: BTreeMap<&'static str, Option<&'static str>>,
}

impl Registry {
    fn build(tax: &Taxonomy) -> Registry {
        let mut tiers = Vec::new();
        let mut alias_index = BTreeMap::new();
        let mut defaults = BTreeMap::new();
        let mut fallback = BTreeMap::new();
        let token_of = |rung: &str| LEGACY_ORDER.into_iter().find(|t| tax.normalize_tier(t).as_deref() == Ok(rung));
        for token in LEGACY_ORDER {
            let entries: Vec<Entry> = tax
                .legacy_registry(token)
                .into_iter()
                .map(|id| Entry {
                    provider: tax.meta(&id).and_then(|m| m.get("provider")).and_then(Value::as_str).unwrap_or("unknown").to_string(),
                    aliases: tax.aliases(&id),
                    id,
                })
                .collect();
            for entry in &entries {
                alias_index.insert(entry.id.to_lowercase(), entry.id.clone());
                for alias in &entry.aliases {
                    alias_index.insert(alias.to_lowercase(), entry.id.clone());
                }
            }
            tiers.push((token, entries));
            if let Some(first) = tax.preferred(LEGACY_SEGMENT, token).into_iter().next() {
                defaults.insert(token, first);
            }
            let next = tax.normalize_tier(token).ok().and_then(|rung| tax.ladder_fallback(&rung)).and_then(|rung| token_of(&rung));
            fallback.insert(token, next);
        }
        Registry { tiers, alias_index, defaults, fallback }
    }

    fn entries(&self, token: &str) -> &[Entry] {
        self.tiers.iter().find(|(t, _)| *t == token).map(|(_, e)| e.as_slice()).unwrap_or(&[])
    }

    /// `normalize_model_id`.
    fn normalize(&self, model: &str) -> String {
        let key = model.trim();
        self.alias_index.get(&key.to_lowercase()).cloned().unwrap_or_else(|| key.to_string())
    }

    /// `expand_unavailable`.
    fn expand(&self, raw: &BTreeSet<String>) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for id in raw {
            let alias = self.normalize(id);
            for (_, entries) in &self.tiers {
                if let Some(entry) = entries.iter().find(|e| e.id == alias) {
                    out.extend(entry.aliases.iter().cloned());
                }
            }
            out.insert(id.clone());
            out.insert(alias);
        }
        out
    }

    fn tier_of(&self, model: &str) -> Option<usize> {
        self.tiers.iter().position(|(_, entries)| entries.iter().any(|e| e.id == model))
    }

    /// `is_below_floor`: the model's known tier is strictly below `floor`.
    fn below_floor(&self, model: &str, floor: &str) -> bool {
        let Some(floor_rank) = LEGACY_ORDER.iter().position(|t| *t == floor) else { return false };
        self.tier_of(model).is_some_and(|rank| rank > floor_rank)
    }

    fn first_available(&self, token: &str, down: &BTreeSet<String>) -> Option<String> {
        self.entries(token).iter().map(|e| &e.id).find(|id| !id.is_empty() && !down.contains(*id)).cloned()
    }
}

pub struct Resolver<'a> {
    tax: &'a Taxonomy,
    reg: Registry,
    build_loop_dir: PathBuf,
    env: EnvView,
    /// Unix seconds, for outage expiry.
    now: f64,
}

fn read_json(path: &Path) -> Option<Value> {
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

/// Python `float(x)` for the shapes a store holds: a number or numeric text.
fn as_f64(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

impl<'a> Resolver<'a> {
    pub fn new(tax: &'a Taxonomy, workdir: &Path, env: EnvView, now: f64) -> Resolver<'a> {
        Resolver { tax, reg: Registry::build(tax), build_loop_dir: workdir.join(".build-loop"), env, now }
    }

    /// `model_index.parse_host`: blank means the default, an expansion to
    /// nothing also means the default.
    pub fn parse_host(&self, raw: Option<&str>) -> Host {
        match self.parse_host_providers(raw) {
            Host::Providers(set) if set.is_empty() => Host::Default,
            other => other,
        }
    }

    /// `model_resolver._parse_host_providers_arg`: an expansion to nothing
    /// stays an empty allowlist.
    pub fn parse_host_providers(&self, raw: Option<&str>) -> Host {
        match raw.map(str::trim) {
            None | Some("") => Host::Default,
            Some(r) if r.eq_ignore_ascii_case("any") => Host::Any,
            Some(r) => Host::Providers(self.tax.expand_host_tokens(r)),
        }
    }

    /// `load_unavailable`: ids of outage records still inside their TTL.
    fn recorded_outages(&self) -> BTreeSet<String> {
        let mut live = BTreeSet::new();
        let Some(Value::Object(data)) = read_json(&self.build_loop_dir.join("model-availability.json")) else { return live };
        let Some(Value::Array(records)) = data.get("unavailable") else { return live };
        for record in records {
            let Value::Object(rec) = record else { continue };
            let id = match rec.get("id") {
                Some(Value::String(s)) => s.trim().to_string(),
                Some(Value::Null) | None => String::new(),
                Some(other) => other.to_string(),
            };
            let Some(recorded_at) = as_f64(rec.get("recorded_at")) else { continue };
            let ttl = as_f64(rec.get("ttl")).map(f64::trunc).filter(|t| *t > 0.0).unwrap_or(DEFAULT_TTL_SECONDS);
            if !id.is_empty() && self.now < recorded_at + ttl {
                live.insert(id);
            }
        }
        live
    }

    /// `detect_host_providers`.
    fn detected_providers(&self) -> Option<BTreeSet<String>> {
        if let Some(raw) = self.env.host_providers.as_deref().filter(|r| !r.trim().is_empty()) {
            return Some(self.tax.expand_host_tokens(raw));
        }
        let names = &self.env.names;
        let truthy = |k: &str| self.env.non_empty.contains(k);
        let prefixed = |p: &str| names.iter().any(|n| n.starts_with(p));
        // `host_capabilities.detect_host(None)`.
        let host = if prefixed("CODEX") {
            "codex"
        } else if truthy("CLAUDE_CODE") || truthy("CLAUDECODE") || names.contains("CLAUDE_PLUGIN_ROOT") {
            "claude_code"
        } else if prefixed("CURSOR") {
            "cursor"
        } else if prefixed("GEMINI") {
            "gemini"
        } else {
            "unknown"
        };
        if let Some(providers) = self.tax.providers_for_host(host) {
            return Some(providers);
        }
        const SIGNALS: [(&[&str], &str); 3] = [
            (&["CLAUDECODE", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_API_KEY"], "anthropic"),
            (&["CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_THREAD_ID", "CODEX_SHELL", "CODEX_CI", "CODEX_SANDBOX", "CODEX_HOME", "OPENAI_API_KEY"], "openai"),
            (&["GEMINI_CLI", "GEMINI_API_KEY", "GOOGLE_API_KEY"], "google"),
        ];
        SIGNALS.iter().find(|(keys, _)| keys.iter().any(|k| truthy(k))).map(|(_, provider)| BTreeSet::from([provider.to_string()]))
    }

    /// `load_host_providers`.
    fn default_providers(&self) -> Option<BTreeSet<String>> {
        if let Some(Value::Object(data)) = read_json(&self.build_loop_dir.join("model-availability.json")) {
            if let Some(Value::Array(list)) = data.get("hostProviders") {
                if !list.is_empty() {
                    return Some(list.iter().map(|p| p.as_str().map(str::to_string).unwrap_or_else(|| p.to_string()).trim().to_lowercase()).filter(|p| !p.is_empty()).collect());
                }
            }
        }
        self.detected_providers()
    }

    /// `_availability_context`: every id that may not be selected, and the
    /// provider allowlist in force.
    fn availability(&self, host: &Host) -> (BTreeSet<String>, Option<BTreeSet<String>>) {
        let mut unavailable = self.reg.expand(&self.recorded_outages());
        let providers = match host {
            Host::Any => None,
            Host::Providers(set) => Some(set.iter().map(|p| p.trim().to_lowercase()).collect()),
            Host::Default => self.default_providers(),
        };
        for id in self.tax.model_ids() {
            let Some(meta) = self.tax.meta(id) else { continue };
            if let Some(allowed) = &providers {
                let provider = meta.get("provider").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
                if !provider.is_empty() && !allowed.contains(&provider) {
                    unavailable.insert(id.to_string());
                }
            }
            if let (Some(ram), Some(need)) = (self.env.ram_gb, meta.get("min_ram_gb").and_then(Value::as_f64)) {
                if need > ram {
                    unavailable.insert(id.to_string());
                }
            }
        }
        (unavailable, providers)
    }

    /// `resolve_model` without a caller fallback: a configured override
    /// (config, then state), else the tier default.
    fn configured(&self, token: &str, agent: Option<&str>) -> (Option<String>, &'static str) {
        for (source, file) in [("config", "config.json"), ("state", "state.json")] {
            let Some(Value::Object(data)) = read_json(&self.build_loop_dir.join(file)) else { continue };
            let config = data.get("config").unwrap_or(&Value::Null);
            let config = if data.contains_key("config") { config.as_object() } else { Some(&data) };
            let Some(Value::Object(overrides)) = config.and_then(|c| c.get("modelOverrides")) else { continue };
            let text = |v: Option<&Value>| v.and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
            let by_agent = agent.and_then(|a| overrides.get("agents")?.as_object()?.get(a)).and_then(|pref| match pref {
                Value::Object(o) => text(o.get("model")),
                other => text(Some(other)),
            });
            if let Some(model) = by_agent.or_else(|| text(overrides.get(token))) {
                return (Some(model), source);
            }
        }
        match self.reg.defaults.get(token) {
            Some(default) => (Some(default.clone()), "tier-default"),
            None => (None, "unresolved"),
        }
    }

    /// The configured override when it may be used: known not to be below
    /// the tier's floor and not unavailable.
    fn usable_override(&self, token: &str, agent: Option<&str>, unavailable: &BTreeSet<String>) -> Option<(String, &'static str)> {
        let (model, source) = self.configured(token, agent);
        if source != "config" && source != "state" {
            return None;
        }
        let model = self.reg.normalize(&model?);
        let floor = self.reg.fallback.get(token).copied().flatten().unwrap_or(token);
        (!self.reg.below_floor(&model, floor) && !unavailable.contains(&model)).then_some((model, source))
    }

    /// `resolve_with_tier_fallback` without a caller fallback.
    fn floor_walk(&self, token: &'static str, unavailable: &BTreeSet<String>) -> (Option<String>, &'static str) {
        let mut unavailable = self.reg.expand(unavailable);
        let (base_model, base_source) = self.configured(token, None);
        let mut model = base_model.as_deref().map(|m| self.reg.normalize(m)).filter(|m| !m.is_empty());
        let floor = self.reg.fallback.get(token).copied().flatten().unwrap_or(token);
        if matches!(base_source, "config" | "state") {
            if let Some(m) = model.take_if(|m| self.reg.below_floor(m, floor)) {
                unavailable.insert(m);
            }
        }
        if let Some(m) = model.filter(|m| !unavailable.contains(m)) {
            return (Some(m), base_source);
        }
        let mut current = token;
        let steps = if token == "frontier" { 1 } else { LEGACY_ORDER.len() };
        for _ in 0..steps {
            let Some(next) = self.reg.fallback.get(current).copied().flatten() else { break };
            if let Some(candidate) = self.reg.first_available(next, &unavailable) {
                return (Some(candidate), "tier-fallback");
            }
            if token == "frontier" {
                return (self.reg.defaults.get(next).cloned(), "tier-fallback");
            }
            current = next;
        }
        (base_model, base_source)
    }

    /// `model_overrides.resolve_role`.
    fn role(&self, segment: &str, rung: &str, agent: Option<&str>, unavailable: &BTreeSet<String>) -> (Option<String>, &'static str) {
        let unavail = self.reg.expand(unavailable);
        let token = LEGACY_ORDER.into_iter().find(|t| self.tax.normalize_tier(t).as_deref() == Ok(rung));
        if let Some(token) = token {
            if let Some((model, source)) = self.usable_override(token, agent, &unavail) {
                return (Some(model), source);
            }
        }
        for id in self.tax.preferred(segment, rung) {
            if !unavail.contains(&self.reg.normalize(&id)) && !unavail.contains(&id) {
                return (Some(id), "role-preferred");
            }
        }
        match token {
            Some(token) if segment == LEGACY_SEGMENT => self.floor_walk(token, &unavail),
            _ => (None, "unresolved"),
        }
    }

    /// `model_resolver.resolve`: the single-axis path of a legacy token.
    fn legacy(&self, token: &'static str, unavailable: &BTreeSet<String>, providers: &Option<BTreeSet<String>>) -> (Option<String>, &'static str) {
        if let Some((model, source)) = self.usable_override(token, None, unavailable) {
            return (Some(model), source);
        }
        let reachable = |provider: &str| match providers {
            Some(allowed) if !provider.is_empty() => allowed.contains(&provider.trim().to_lowercase()),
            _ => true,
        };
        let mut candidates: Vec<String> = Vec::new();
        for entry in self.reg.entries(token) {
            if !entry.id.is_empty() && !candidates.contains(&entry.id) && reachable(&entry.provider) {
                candidates.push(entry.id.clone());
            }
        }
        for (id, entry) in self.tier_cache() {
            let text = |key: &str| entry.get(key).and_then(Value::as_str).unwrap_or("");
            if text("tier") == token && text("provenance") == "verified" && !candidates.contains(&id) && reachable(text("provider")) {
                candidates.push(id);
            }
        }
        match candidates.into_iter().find(|id| !unavailable.contains(id)) {
            Some(id) => (Some(id), "in-tier-chain"),
            None => self.floor_walk(token, unavailable),
        }
    }

    /// `load_tier_cache`, in file order (Python dict order).
    fn tier_cache(&self) -> Vec<(String, Value)> {
        let Ok(text) = std::fs::read_to_string(self.build_loop_dir.join("model-tier-cache.json")) else { return Vec::new() };
        let Ok(Value::Object(mut data)) = serde_json::from_str::<Value>(&text) else { return Vec::new() };
        taxonomy::top_level_keys(&text)
            .into_iter()
            .filter_map(|key| data.remove(&key).map(|v| (key, v)))
            .filter(|(_, v)| v.get("tier").is_some_and(|t| !matches!(t, Value::Null | Value::Bool(false)) && t.as_str() != Some("")))
            .collect()
    }

    fn finish(&self, picked: (Option<String>, &'static str), unavailable: &BTreeSet<String>) -> Resolution {
        let (model, source) = picked;
        let resolved = model.as_deref().map(|m| self.reg.normalize(m)).is_some_and(|m| !m.is_empty() && !unavailable.contains(&m));
        Resolution { model, source: source.to_string(), resolved }
    }

    /// What `model_index.py resolve --tier T [--segment S] [--host H]`
    /// selects. `Err` is its not-found message (unknown tier or segment).
    pub fn resolve(&self, tier: &str, segment: Option<&str>, host: &Host) -> Result<Resolution, String> {
        let rung = self.tax.normalize_tier(tier)?;
        if let Some(segment) = segment {
            if !self.tax.has_segment(segment) {
                return Err(format!("unknown segment {segment:?}"));
            }
        }
        let (unavailable, providers) = self.availability(host);
        let picked = match (segment, LEGACY_ORDER.into_iter().find(|t| *t == tier)) {
            (Some(segment), _) => self.role(segment, &rung, None, &unavailable),
            (None, Some(token)) if self.tax.is_legacy_tier(tier) => self.legacy(token, &unavailable, &providers),
            (None, _) => self.role(LEGACY_SEGMENT, &rung, None, &unavailable),
        };
        Ok(self.finish(picked, &unavailable))
    }

    /// What `resolve_agent_model.py <agent>` selects from the frontmatter of
    /// `<agents_dir>/<agent>.md`. An `inherit` agent resolves to `inherit`.
    pub fn resolve_agent(&self, agent: &str, agents_dir: &Path, host: &Host) -> Result<Resolution, String> {
        let path = agents_dir.join(format!("{agent}.md"));
        let text = std::fs::read_to_string(&path).map_err(|_| format!("agent file not found: {}", path.display()))?;
        let fm = frontmatter(&text);
        let field = |key: &str| fm.get(key).map(String::as_str).filter(|v| !v.is_empty());
        let (segment, tier, fm_model) = (field("segment"), field("tier"), field("model"));
        if [segment, tier, fm_model].contains(&Some("inherit")) {
            return Ok(Resolution { model: Some("inherit".into()), source: "inherit".into(), resolved: true });
        }
        let (unavailable, _) = self.availability(host);
        if let (Some(segment), Some(tier)) = (segment, tier) {
            if let (true, Ok(rung)) = (self.tax.has_segment(segment), self.tax.normalize_tier(tier)) {
                let got = self.finish(self.role(segment, &rung, Some(agent), &unavailable), &unavailable);
                return Ok(if got.resolved { got } else { Resolution { model: None, source: "unresolved".into(), resolved: false } });
            }
        }
        let default = tier.and_then(|t| LEGACY_ORDER.into_iter().find(|l| *l == t)).and_then(|t| self.reg.defaults.get(t)).map(String::as_str);
        for (candidate, source) in [(fm_model, "frontmatter-fallback"), (default, "tier-default-fallback")] {
            let Some(model) = candidate.map(|m| self.reg.normalize(m)).filter(|m| !m.is_empty()) else { continue };
            if !unavailable.contains(&model) {
                return Ok(Resolution { model: Some(model), source: source.into(), resolved: true });
            }
        }
        Ok(Resolution { model: None, source: "unresolved".into(), resolved: false })
    }
}

/// `resolve_agent_model._parse_frontmatter`: top-level `key: value` scalars
/// of the leading `---` block.
pub fn frontmatter(text: &str) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let mut lines = text.lines();
    if lines.next().map(str::trim) != Some("---") {
        return out;
    }
    let mut in_block = false;
    for raw in lines {
        if raw.trim() == "---" {
            break;
        }
        let indented = raw.chars().next().is_none_or(char::is_whitespace);
        if in_block && indented {
            continue;
        }
        in_block = false;
        if indented {
            continue;
        }
        let Some((key, value)) = raw.split_once(':') else { continue };
        let mut value = value.trim();
        if ["|", ">", "|-", ">-"].contains(&value) {
            in_block = true;
            continue;
        }
        let bytes = value.as_bytes();
        if bytes.len() >= 2 && bytes[0] == bytes[bytes.len() - 1] && (bytes[0] == b'"' || bytes[0] == b'\'') {
            value = &value[1..value.len() - 1];
        }
        out.insert(key.trim().to_string(), value.to_string());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const TAX: &str = r#"{
      "tiers": {"order": ["T1","T2","T3","T4","T-S"],
        "fallback": {"_comment": "x", "T1": "T2", "T2": "T3", "T3": "T4", "T4": null, "T-S": null},
        "legacy_aliases": {"_comment": "x", "frontier": "T1", "thinking": "T2", "code": "T3", "pattern": "T4"}},
      "segments": {"generative_reasoning": {}, "agentic_execution": {}},
      "hosts": {"claude_code": {"providers": ["anthropic"], "aliases": ["claude"]}, "codex": {"providers": ["openai"], "aliases": ["openai"]}},
      "preferred": {"generative_reasoning": {"T1": ["big", "old", "gx"], "T2": ["big", "gx"], "T3": ["mid", "gy"], "T4": ["small"]},
                    "agentic_execution": {"T3": ["mid", "local"]}},
      "legacy_registry": {"frontier": ["big", "old", "gx"], "thinking": ["big", "gy"], "code": ["mid", "gy"], "pattern": ["small"]},
      "models": {"_comment": "x",
        "big": {"provider": "anthropic", "aliases": ["claude-big-5"]}, "old": {"provider": "anthropic"},
        "mid": {"provider": "anthropic"}, "small": {"provider": "anthropic"},
        "gx": {"provider": "openai"}, "gy": {"provider": "openai"},
        "local": {"provider": "local", "min_ram_gb": 64}}
    }"#;

    fn dir(files: &[(&str, &str)]) -> PathBuf {
        static N: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
        let d = std::env::temp_dir().join(format!("bl-route-{}-{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
        std::fs::create_dir_all(d.join(".build-loop")).unwrap();
        for (name, body) in files {
            std::fs::write(d.join(".build-loop").join(name), body).unwrap();
        }
        d
    }

    fn model(files: &[(&str, &str)], tier: &str, segment: Option<&str>, host: &str) -> (Option<String>, String, bool) {
        let tax = Taxonomy::parse(TAX).unwrap();
        let d = dir(files);
        let r = Resolver::new(&tax, &d, EnvView::default(), 1000.0);
        let got = r.resolve(tier, segment, &r.parse_host(Some(host))).unwrap();
        std::fs::remove_dir_all(d).unwrap();
        (got.model, got.source, got.resolved)
    }

    #[test]
    fn preferred_order_and_host_filter() {
        assert_eq!(model(&[], "T1", None, "claude").0.as_deref(), Some("big"));
        assert_eq!(model(&[], "frontier", None, "codex"), (Some("gx".into()), "in-tier-chain".into(), true));
        assert_eq!(model(&[], "T3", Some("agentic_execution"), "any").0.as_deref(), Some("mid"));
    }

    #[test]
    fn an_outage_by_canonical_id_moves_to_the_next_and_expires() {
        let down = r#"{"unavailable": [{"id": "claude-big-5", "recorded_at": 900, "ttl": 500}]}"#;
        assert_eq!(model(&[("model-availability.json", down)], "T1", None, "claude").0.as_deref(), Some("old"));
        let expired = r#"{"unavailable": [{"id": "big", "recorded_at": 100, "ttl": 500}, "big"]}"#;
        assert_eq!(model(&[("model-availability.json", expired)], "T1", None, "claude").0.as_deref(), Some("big"));
    }

    #[test]
    fn frontier_never_resolves_below_thinking() {
        let down = r#"{"unavailable": [{"id": "big", "recorded_at": 900}, {"id": "old", "recorded_at": 900}]}"#;
        // The whole thinking tier is down too: the thinking default is the floor marker.
        assert_eq!(model(&[("model-availability.json", down)], "frontier", None, "claude"), (Some("big".into()), "tier-fallback".into(), false));
        // A lower tier keeps walking.
        assert_eq!(model(&[("model-availability.json", down)], "thinking", None, "claude"), (Some("mid".into()), "tier-fallback".into(), true));
    }

    #[test]
    fn config_override_wins_unless_below_floor() {
        let cfg = r#"{"modelOverrides": {"frontier": "gx", "code": "small"}}"#;
        assert_eq!(model(&[("config.json", cfg)], "T1", None, "any"), (Some("gx".into()), "config".into(), true));
        // Unreachable on this host: falls through to the list.
        assert_eq!(model(&[("config.json", cfg)], "T1", None, "claude").0.as_deref(), Some("big"));
        assert_eq!(model(&[("config.json", cfg)], "code", None, "any").0.as_deref(), Some("small"));
        let low = r#"{"config": {"modelOverrides": {"frontier": "small"}}}"#;
        assert_eq!(model(&[("state.json", low)], "frontier", None, "any").0.as_deref(), Some("big"));
    }

    #[test]
    fn verified_cache_entries_join_the_legacy_chain_in_file_order() {
        let down = r#"{"unavailable": [{"id": "small", "recorded_at": 900}]}"#;
        let cache = r#"{"zeta": {"tier": "pattern", "provenance": "verified"}, "guess": {"tier": "pattern", "provenance": "unverified"}, "alpha": {"tier": "pattern", "provenance": "verified"}}"#;
        assert_eq!(model(&[("model-availability.json", down), ("model-tier-cache.json", cache)], "pattern", None, "any").0.as_deref(), Some("zeta"));
    }

    #[test]
    fn ram_and_unknown_tokens() {
        let tax = Taxonomy::parse(TAX).unwrap();
        let d = dir(&[("model-availability.json", r#"{"unavailable": [{"id": "mid", "recorded_at": 900}]}"#)]);
        let small = Resolver::new(&tax, &d, EnvView { ram_gb: Some(16.0), ..Default::default() }, 1000.0);
        assert_eq!(small.resolve("T3", Some("agentic_execution"), &Host::Any).unwrap().model, None);
        let large = Resolver::new(&tax, &d, EnvView { ram_gb: Some(128.0), ..Default::default() }, 1000.0);
        assert_eq!(large.resolve("T3", Some("agentic_execution"), &Host::Any).unwrap().model.as_deref(), Some("local"));
        assert!(large.resolve("T9", None, &Host::Any).is_err());
        assert!(large.resolve("T1", Some("nope"), &Host::Any).is_err());
        std::fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn host_detection_follows_the_environment() {
        let tax = Taxonomy::parse(TAX).unwrap();
        let d = dir(&[]);
        let env = |names: &[&str]| EnvView { names: names.iter().map(|s| s.to_string()).collect(), non_empty: names.iter().map(|s| s.to_string()).collect(), ..Default::default() };
        let pick = |e: EnvView| Resolver::new(&tax, &d, e, 0.0).resolve("frontier", None, &Host::Default).unwrap().model;
        assert_eq!(pick(env(&["CODEX_HOME"])).as_deref(), Some("gx"));
        assert_eq!(pick(env(&["CLAUDECODE"])).as_deref(), Some("big"));
        assert_eq!(pick(EnvView { host_providers: Some("openai".into()), ..env(&["CLAUDECODE"]) }).as_deref(), Some("gx"));
        std::fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn frontmatter_reads_top_level_scalars_only() {
        let fm = frontmatter("---\nname: x\ndescription: |\n  tier: wrong\n  more\nsegment: \"agentic_execution\"\ntier: code\ntools:\n  - model: no\n---\ntier: body\n");
        assert_eq!(fm.get("segment").map(String::as_str), Some("agentic_execution"));
        assert_eq!(fm.get("tier").map(String::as_str), Some("code"));
        assert!(!fm.contains_key("model"));
    }
}
