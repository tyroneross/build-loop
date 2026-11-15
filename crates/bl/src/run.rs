// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl run --profile <name-or-path> [--model <id>] [--work <kind>]
//! [--cwd <dir>] [--dry-run] [--json] [--summary <file>] [--allow-unasked]
//! "<task>"` — EXPERIMENTAL.
//!
//! Drives one agent session over the Agent Client Protocol: load a use-case
//! profile, resolve the model natively (`bl-route`), start the profile's
//! agent, append the build-loop method to its instructions, answer every
//! permission request from the profile's policy (`bl-gate`), and record
//! tool-trace lines in the format `bl hook trace` writes.
//!
//! Output: the agent's text on stdout; the experimental notice, one line
//! per permission request with its decision, and the failure reason on
//! stderr. With `--json`, stdout carries one JSON summary line instead
//! (stage timings, tool calls, decisions) and the text moves to stderr;
//! `--summary <file>` writes that same line to a file and leaves stdout as
//! it was.
//!
//! Exit: 0 when the turn ended normally (`end_turn`); 1 with a one-line
//! reason otherwise; 2 for a usage or profile error; 124 when the profile's
//! deadline expired; 130/143 on SIGINT/SIGTERM. On every path the agent's
//! process group is killed first.
//!
//! The policy sees only the tool calls the agent asks about. Whether an
//! agent asks before every action is a property of that agent's
//! configuration (for the Claude adapter: `ask` rules passed in the
//! profile's `session_meta`), not something this client can enforce. So:
//! a profile without `ask` rules, or with a mode that does not ask, is
//! refused unless `--allow-unasked` is given; every tool call that starts
//! without a preceding permission request is reported on stderr as it
//! happens; and the run notice names the profile file and the exact
//! command started. Tool input is passed through the trace redaction
//! before it is printed or stored.

use crate::profile::Profile;
use crate::{cli, sys, trace};
use bl_acp::agent::{Agent, Launch};
use bl_acp::rpc::{Handler, RpcError};
use bl_acp::session::{self, NewSession, PermissionRequest};
use bl_gate::{Policy, Request};
use bl_route::{EnvView, Resolver, Taxonomy};
use serde_json::{json, Value};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// Appended to the agent's instructions when the profile sets
/// `instructions.method`. A fixed excerpt of the repository's `AGENTS.md`
/// (phases, core principles, autonomy gate); skills are not loaded.
pub const METHOD_EXCERPT: &str = "\
You are driven by build-loop. Follow the build-loop method (excerpt of its AGENTS.md):
- Phases for a change that spans files: Assess (state, goal, 3-5 pass/fail criteria), Plan (tasks in dependency order), Execute, Review (validate against the criteria with evidence), Iterate on failures (at most 5 times). Skip the loop for single-file edits, config changes and quick fixes under about 20 lines.
- After any issue, reassess before retrying. Diagnose before fixing: many errors sharing a pattern are one system problem.
- KISS and DRY: before adding anything, try to delete something, extend what exists, or do nothing.
- No false data: no mock data in production, no hardcoded metrics, no unverified claims. Say what you checked and how.
- Keep going until done within the task you were given. Stop and ask only before a production push, an irreversible delete, or a major user-impacting decision.
- Commit authorship stays human: never set an agent as git author or committer.";

const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Everything decided before an agent is started; what `--dry-run` prints.
struct Plan {
    profile: Profile,
    project: PathBuf,
    model: String,
    model_source: String,
    /// Other ids the taxonomy knows the model by, tried when the agent does
    /// not offer the id itself.
    model_aliases: Vec<String>,
    instructions: String,
    task: String,
    json: bool,
    summary: Option<PathBuf>,
    allow_unasked: bool,
}

impl Plan {
    /// The exact command and arguments that are started.
    fn command_line(&self) -> String {
        std::iter::once(&self.profile.command).chain(self.profile.args.iter()).map(|a| format!("{a:?}")).collect::<Vec<_>>().join(" ")
    }

    fn describe(&self) -> Value {
        json!({
            "experimental": true,
            "profile": self.profile.name,
            "profile_path": self.profile.path,
            "agent_command": std::iter::once(self.profile.command.clone()).chain(self.profile.args.iter().cloned()).collect::<Vec<_>>(),
            "sign_in": self.profile.sign_in,
            "model": self.model,
            "model_source": self.model_source,
            "policy": self.profile.policy,
            "mode": self.profile.mode,
            "instruction_chars": self.instructions.chars().count(),
            "mcp_servers": self.profile.mcp_servers.as_array().map(Vec::len).unwrap_or(0),
            "skills": self.profile.skills.len(),
            "project": self.project,
            "deadline_seconds": self.profile.deadline_seconds,
            "unset_env": self.profile.unset_env,
            "unasked_risk": self.profile.unasked_risk(),
        })
    }
}

fn plan(args: &[String]) -> Result<(Plan, bool), String> {
    let p = cli::parse(args, &["--profile", "--model", "--work", "--cwd", "--taxonomy", "--summary"], &["--dry-run", "--json", "--allow-unasked"])?;
    let dry_run = p.switch("--dry-run");
    let task = match p.positional.as_slice() {
        [task] if !task.trim().is_empty() => task.clone(),
        [] if dry_run => String::new(),
        _ => return Err("usage: bl run --profile <name-or-path> [--model <id>] [--work <kind>] [--cwd <dir>] [--dry-run] [--json] [--summary <file>] [--allow-unasked] \"<task>\"".into()),
    };
    let project = match p.value("--cwd") {
        Some(dir) => PathBuf::from(dir),
        None => std::env::current_dir().map_err(|e| format!("no current directory: {e}"))?,
    };
    let project = project.canonicalize().map_err(|e| format!("project directory {}: {e}", project.display()))?;
    let root = cli::root();
    let profile = Profile::load(p.value("--profile").ok_or("--profile is required")?, root.as_deref())?;
    if bl_gate::by_name(&profile.policy).is_none() {
        return Err(format!("profile {:?} names policy {:?}, which this build does not have", profile.name, profile.policy));
    }
    if !profile.skills.is_empty() {
        return Err(format!("profile {:?} lists skills; exposing skills is not built in this slice", profile.name));
    }
    let taxonomy = cli::taxonomy_path(p.value("--taxonomy")).and_then(|path| Taxonomy::load(&path));
    let (model, model_source) = match p.value("--model") {
        Some(model) => (model.to_string(), "--model".to_string()),
        None => {
            let rule = profile.model_rule(p.value("--work").unwrap_or("default"))?;
            let resolver = Resolver::new(taxonomy.as_ref().map_err(String::clone)?, &project, EnvView::from_process(), crate::route_cmd::now());
            let got = resolver.resolve(&rule.tier, Some(&rule.segment), &resolver.parse_host(Some(&profile.host)))?;
            match got.model.filter(|_| got.resolved) {
                Some(model) => (model, format!("{}/{} on {} ({})", rule.segment, rule.tier, profile.host, got.source)),
                None => return Err(format!("no available model for {}/{} on host {}", rule.segment, rule.tier, profile.host)),
            }
        }
    };
    let model_aliases = taxonomy.map(|t| t.aliases(&model)).unwrap_or_default();
    let mut parts: Vec<&str> = Vec::new();
    if profile.method {
        parts.push(METHOD_EXCERPT);
    }
    parts.extend(profile.append.iter().map(String::as_str));
    let instructions = parts.join("\n\n");
    Ok((Plan { profile, project, model, model_source, model_aliases, instructions, task, json: p.switch("--json"), summary: p.value("--summary").map(PathBuf::from), allow_unasked: p.switch("--allow-unasked") }, dry_run))
}

#[derive(Default)]
struct Tool {
    id: String,
    name: String,
    kind: String,
    title: String,
    input: Value,
    parent: Option<String>,
    /// A permission request for this call reached the policy.
    requested: bool,
    /// The agent reported the call running or finished before any request.
    ran_unasked: bool,
    allowed: Option<bool>,
    rule: &'static str,
    status: String,
    gate_us: Option<u128>,
    traced_start: bool,
    traced_end: bool,
}

struct Driver<'a> {
    started: Instant,
    policy: Box<dyn Policy>,
    project: &'a Path,
    json: bool,
    session_id: String,
    first_text_ms: Option<u128>,
    tools: Vec<Tool>,
    ignored_requests: Vec<String>,
}

impl Driver<'_> {
    fn tool(&mut self, id: &str) -> &mut Tool {
        let at = match self.tools.iter().position(|t| t.id == id) {
            Some(at) => at,
            None => {
                self.tools.push(Tool { id: id.to_string(), ..Default::default() });
                self.tools.len() - 1
            }
        };
        &mut self.tools[at]
    }

    /// Fold a `tool_call`, `tool_call_update` or a request's `toolCall`
    /// into the record of that call.
    fn absorb(&mut self, call: &Value) -> Option<String> {
        let id = call.get("toolCallId")?.as_str()?.to_string();
        let tool = self.tool(&id);
        let text = |key: &str| call.get(key).and_then(Value::as_str).filter(|s| !s.is_empty());
        // The tool's own name: the adapter's `name`, else its `_meta` copy.
        if let Some(name) = text("name").or_else(|| call.pointer("/_meta/claudeCode/toolName").and_then(Value::as_str)) {
            tool.name = name.to_string();
        }
        if let Some(kind) = text("kind") {
            tool.kind = kind.to_string();
        }
        if let Some(title) = text("title") {
            tool.title = title.to_string();
        }
        if let Some(parent) = call.pointer("/_meta/claudeCode/parentToolUseId").and_then(Value::as_str) {
            tool.parent = Some(parent.to_string());
        }
        if let Some(input) = call.get("rawInput").filter(|i| i.as_object().is_some_and(|o| !o.is_empty())) {
            tool.input = input.clone();
        }
        if let Some(status) = text("status") {
            if status != "pending" && !tool.requested && !tool.ran_unasked {
                tool.ran_unasked = true;
                let name = if tool.name.is_empty() { tool.kind.clone() } else { tool.name.clone() };
                eprintln!("\nbl run: UNASKED tool call {name} {:?} started without a permission request; the policy did not see it", shown(&Value::String(tool.title.clone())));
            }
            tool.status = status.to_string();
        }
        Some(id)
    }

    /// One trace line in the `bl hook trace` format, built from the same
    /// hook-shaped payload. Content the native trace path would hand to
    /// Python (possible secrets, floats) is left out instead.
    fn trace(&self, tool: &Tool, phase: &str) {
        let mut payload = json!({"tool_name": if tool.name.is_empty() { &tool.kind } else { &tool.name }, "session_id": self.session_id, "tool_use_id": tool.id});
        if phase == "start" && !tool.input.is_null() {
            payload["tool_input"] = tool.input.clone();
        }
        let workdir = self.project.to_string_lossy();
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        let line = match trace::plan(&workdir, phase, payload.to_string().as_bytes(), now) {
            Ok(line) => line,
            Err(_) => {
                payload.as_object_mut().map(|o| o.remove("tool_input"));
                trace::plan(&workdir, phase, payload.to_string().as_bytes(), now).ok().flatten()
            }
        };
        if let Some(line) = line {
            let _ = trace::append(self.project, line.as_bytes());
        }
    }

    fn trace_progress(&mut self, id: &str) {
        let Some(at) = self.tools.iter().position(|t| t.id == id) else { return };
        let t = &self.tools[at];
        let terminal = matches!(t.status.as_str(), "completed" | "failed");
        if !t.traced_start && (t.requested || terminal || t.status == "in_progress") {
            self.tools[at].traced_start = true;
            self.trace(&self.tools[at], "start");
        }
        if terminal && !self.tools[at].traced_end {
            self.tools[at].traced_end = true;
            self.trace(&self.tools[at], if self.tools[at].status == "failed" { "error" } else { "end" });
        }
    }
}

impl Handler for Driver<'_> {
    fn notification(&mut self, method: &str, params: &Value) {
        if method != "session/update" {
            return;
        }
        let update = params.get("update").unwrap_or(&Value::Null);
        match update.get("sessionUpdate").and_then(Value::as_str) {
            Some("agent_message_chunk") => {
                if let Some(text) = update.pointer("/content/text").and_then(Value::as_str) {
                    if self.first_text_ms.is_none() && !text.is_empty() {
                        self.first_text_ms = Some(self.started.elapsed().as_millis());
                    }
                    if self.json {
                        eprint!("{text}");
                    } else {
                        print!("{text}");
                        let _ = std::io::stdout().flush();
                    }
                }
            }
            Some("tool_call") | Some("tool_call_update") => {
                if let Some(id) = self.absorb(update) {
                    self.trace_progress(&id);
                }
            }
            _ => {}
        }
    }

    fn request(&mut self, method: &str, params: &Value) -> Result<Value, RpcError> {
        if method != "session/request_permission" {
            self.ignored_requests.push(method.to_string());
            return Err(RpcError::method_not_found(method));
        }
        let received = Instant::now();
        let request = PermissionRequest::parse(params);
        let id = self.absorb(params.get("toolCall").unwrap_or(&Value::Null)).unwrap_or_default();
        let (name, kind) = {
            let tool = self.tool(&id);
            (tool.name.clone(), if request.kind.is_empty() { tool.kind.clone() } else { request.kind.clone() })
        };
        let decision = self.policy.decide(&Request { kind: &kind, tool: &name, input: &request.raw_input, locations: &request.locations, project: self.project });
        let reply = request.reply(decision.allow);
        let gate_us = received.elapsed().as_micros();
        let tool = self.tool(&id);
        tool.requested = true;
        tool.allowed = Some(decision.allow);
        tool.rule = decision.rule;
        tool.gate_us = Some(gate_us);
        let what = shown(request.raw_input.get("command").filter(|c| c.is_string()).unwrap_or(&Value::String(request.title.clone())));
        let verdict = if decision.allow { "allow" } else { "refuse" };
        eprintln!("\nbl run: permission [{kind}] {name} {what:?} -> {verdict} ({})", decision.rule);
        self.trace_progress(&id);
        Ok(reply)
    }
}

/// What may be printed or stored of tool input: the value after the
/// redaction `bl hook trace` applies. Sensitive keys and inline credentials
/// are replaced; any unsupported redaction is withheld whole.
fn redacted(input: &Value) -> Value {
    match input {
        Value::Object(map) => Value::Object(map.iter().map(|(k, v)| (shown(&Value::String(k.clone())), trace::redact(&Value::Null, k).ok().filter(Value::is_string).unwrap_or_else(|| redacted(v)))).collect()),
        Value::Array(items) => Value::Array(items.iter().map(redacted).collect()),
        other => trace::redact(other, "").unwrap_or_else(|_| Value::String(WITHHELD.into())),
    }
}

const WITHHELD: &str = "[withheld: may contain a credential]";

/// A redacted value as one printable string.
fn shown(value: &Value) -> String {
    match redacted(value) {
        Value::String(s) => s,
        other => other.to_string(),
    }
}

/// Owns the running agent: on every way out its process group is killed,
/// then forgotten, then reaped, in that order, so no signal is ever sent to
/// a group id that has been reaped and may have been reused.
struct Running(Agent);

impl Drop for Running {
    fn drop(&mut self) {
        self.0.kill_group();
        sys::agent_gone();
        self.0.kill();
    }
}

/// The agent's value for a taxonomy model id: the id itself when the agent
/// offers it, else the first of its taxonomy aliases the agent offers.
fn agent_model_value(model: &str, offered: &[String], aliases: &[String]) -> Option<String> {
    std::iter::once(model).chain(aliases.iter().map(String::as_str)).find(|c| offered.iter().any(|o| o == c)).map(str::to_string)
}

fn fail(what: &str, e: impl std::fmt::Display, agent: &Agent) -> String {
    let tail = agent.stderr_tail();
    let last = tail.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("");
    if last.is_empty() {
        format!("{what}: {e}")
    } else {
        format!("{what}: {e} (agent stderr: {})", last.chars().take(300).collect::<String>())
    }
}

fn drive(plan: &Plan, started: Instant) -> Result<i32, String> {
    let profile = &plan.profile;
    let mut env = profile.env.clone();
    if let Some(name) = &profile.model_env {
        env.push((name.clone(), plan.model.clone()));
    }
    let mut stage = serde_json::Map::new();
    let mark = |stage: &mut serde_json::Map<String, Value>, name: &str| {
        stage.insert(name.to_string(), json!(started.elapsed().as_micros() as f64 / 1000.0));
    };
    mark(&mut stage, "ready_ms");
    // Start and register under the exit lock and the spawning flag: the
    // deadline thread waits, and a signal is held until the group is known.
    let mut running = {
        let _guard = crate::EXIT.lock();
        sys::agent_starting();
        let spawned = Agent::spawn(&Launch { command: profile.command.clone(), args: profile.args.clone(), env, env_remove: profile.unset_env.clone() });
        sys::agent_started(spawned.as_ref().map(Agent::pid).unwrap_or(0));
        Running(spawned.map_err(|e| format!("cannot start agent {:?}: {e}", profile.command))?)
    };
    let agent = &mut running.0;
    mark(&mut stage, "spawned_ms");

    let mut driver = Driver {
        started,
        policy: bl_gate::by_name(&profile.policy).expect("checked in plan"),
        project: &plan.project,
        json: plan.json,
        session_id: String::new(),
        first_text_ms: None,
        tools: Vec::new(),
        ignored_requests: Vec::new(),
    };

    let init = session::initialize(&mut agent.conn, &mut driver, CLIENT_VERSION).map_err(|e| fail("initialize", e, agent))?;
    mark(&mut stage, "initialized_ms");
    if !plan.instructions.is_empty() && !init.system_prompt {
        return Err(format!("agent {} {} does not accept appended instructions (no systemPrompt capability)", init.agent_name, init.agent_version));
    }
    let cwd = plan.project.to_string_lossy();
    let request = NewSession { cwd: &cwd, mcp_servers: &profile.mcp_servers, append_instructions: Some(&plan.instructions), meta: Some(&profile.session_meta) };
    let mut sess = session::new_session(&mut agent.conn, &mut driver, &request).map_err(|e| fail("session/new", e, agent))?;
    driver.session_id = sess.id.clone();
    mark(&mut stage, "session_ms");

    if let Some(mode) = &profile.mode {
        let option = sess.option("mode").cloned().ok_or_else(|| format!("profile sets mode {mode:?} but the agent offers no mode option"))?;
        if &option.current != mode {
            if !option.values.contains(mode) {
                return Err(format!("agent does not offer mode {mode:?} (offers: {})", option.values.join(", ")));
            }
            session::set_config_option(&mut agent.conn, &mut driver, &mut sess, &option.id, mode).map_err(|e| fail("set mode", e, agent))?;
        }
    }
    let option = sess.option("model").cloned().ok_or("the agent offers no model option")?;
    let value = agent_model_value(&plan.model, &option.values, &plan.model_aliases).ok_or_else(|| format!("agent does not offer model {:?} (offers: {})", plan.model, option.values.join(", ")))?;
    if option.current != value {
        session::set_config_option(&mut agent.conn, &mut driver, &mut sess, &option.id, &value).map_err(|e| fail("set model", e, agent))?;
    }
    let model_in_use = sess.option("model").map(|o| o.current.clone()).unwrap_or_default();
    let mode_in_use = sess.option("mode").map(|o| o.current.clone()).unwrap_or_default();
    mark(&mut stage, "configured_ms");

    let turn = session::prompt(&mut agent.conn, &mut driver, &sess, &plan.task);
    mark(&mut stage, "done_ms");
    if let Some(ms) = driver.first_text_ms {
        stage.insert("first_text_ms".into(), json!(ms));
    }
    let outcome = match &turn {
        Ok(stop) if stop == "end_turn" => Ok(()),
        Ok(stop) => Err(format!("turn stopped: {}", if stop.is_empty() { "no stopReason" } else { stop })),
        Err(e) => Err(fail("session/prompt", e, agent)),
    };
    let dropped = agent.conn.dropped_lines;
    drop(running);

    if !plan.json {
        println!();
    }
    let tools: Vec<Value> = driver
        .tools
        .iter()
        .map(|t| {
            json!({
                "id": t.id, "tool": t.name, "kind": t.kind, "title": shown(&Value::String(t.title.clone())), "input": redacted(&t.input), "parent": t.parent,
                "requested": t.requested, "ran_unasked": t.ran_unasked,
                "decision": t.allowed.map(|a| if a { "allow" } else { "refuse" }), "rule": t.rule,
                "status": t.status, "gate_us": t.gate_us,
            })
        })
        .collect();
    if plan.json || plan.summary.is_some() {
        let summary = json!({
            "ok": outcome.is_ok(), "reason": outcome.as_ref().err(), "stop_reason": turn.as_ref().ok(),
            "profile": profile.name, "policy": profile.policy, "model": plan.model, "agent_model": model_in_use, "mode": mode_in_use,
            "agent": {"name": init.agent_name, "version": init.agent_version},
            "session_id": sess.id, "stages": stage, "tools": tools,
            "dropped_lines": dropped, "ignored_requests": driver.ignored_requests,
        });
        if plan.json {
            println!("{summary}");
        }
        if let Some(path) = &plan.summary {
            std::fs::write(path, format!("{summary}\n")).map_err(|e| format!("cannot write summary {}: {e}", path.display()))?;
        }
    }
    outcome.map(|()| 0)
}

pub fn main(started: Instant, args: &[String]) -> i32 {
    let (plan, dry_run) = match plan(args) {
        Ok(planned) => planned,
        Err(message) => {
            eprintln!("bl run: {message}");
            return 2;
        }
    };
    if dry_run {
        let d = plan.describe();
        if plan.json {
            println!("{d}");
        } else {
            println!("bl run (experimental) dry run; nothing started");
            println!("  starts: {}", plan.command_line());
            for key in ["profile", "profile_path", "sign_in", "unset_env", "model", "model_source", "policy", "mode", "unasked_risk", "instruction_chars", "mcp_servers", "skills", "project", "deadline_seconds"] {
                let shown = match &d[key] {
                    Value::String(s) => s.clone(),
                    Value::Array(a) => a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(" "),
                    other => other.to_string(),
                };
                println!("  {key}: {shown}");
            }
        }
        return 0;
    }
    match (plan.profile.unasked_risk(), plan.allow_unasked) {
        (Some(risk), false) => {
            eprintln!("bl run: refusing to start: {risk}. Pass --allow-unasked to run anyway.");
            return 2;
        }
        (Some(risk), true) => eprintln!("bl run: WARNING (--allow-unasked): {risk}."),
        (None, _) => {}
    }
    eprintln!(
        "bl run: EXPERIMENTAL slice. Policy {} is a placeholder, not a safety gate; it sees only what the agent asks about. profile={} ({}) model={} starts: {}",
        plan.profile.policy,
        plan.profile.name,
        plan.profile.path.display(),
        plan.model,
        plan.command_line()
    );
    let deadline = started + Duration::from_secs(plan.profile.deadline_seconds);
    let seconds = plan.profile.deadline_seconds;
    std::thread::spawn(move || {
        std::thread::sleep(deadline.saturating_duration_since(Instant::now()));
        let _guard = crate::EXIT.lock();
        sys::kill_registered_group();
        eprintln!("\nbl run: deadline of {seconds}s expired; agent killed");
        std::process::exit(124);
    });
    sys::kill_group_on_signals();
    let code = match std::panic::catch_unwind(|| drive(&plan, started)) {
        Ok(Ok(code)) => code,
        Ok(Err(reason)) => {
            eprintln!("\nbl run: {}", reason.replace('\n', " "));
            1
        }
        Err(_) => {
            eprintln!("\nbl run: internal error");
            1
        }
    };
    let _guard = crate::EXIT.lock();
    sys::kill_registered_group();
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_agent_value_prefers_the_id_then_an_alias() {
        let offered = vec!["default".to_string(), "sonnet".to_string(), "claude-opus-5".to_string()];
        assert_eq!(agent_model_value("sonnet", &offered, &["claude-sonnet-5".into()]).as_deref(), Some("sonnet"));
        assert_eq!(agent_model_value("opus", &offered, &["claude-opus-5".into()]).as_deref(), Some("claude-opus-5"));
        assert_eq!(agent_model_value("gpt-6.1-sol", &offered, &[]), None);
    }

    /// Finding 7: tool input goes through the trace redaction.
    #[test]
    fn tool_input_is_redacted_before_it_is_shown_or_stored() {
        let input = json!({"command": "curl -H 'Authorization: Bearer abc123' https://x", "api_key": "sk-live-1", "nested": {"password": "fixture-password", "path": "a.txt"}, "list": ["ok", "token=fixture-token"]});
        let out = redacted(&input);
        assert_eq!(out["command"], "curl -H 'Authorization: [REDACTED]' https://x");
        assert_eq!(out["api_key"], "[REDACTED]");
        assert_eq!(out["nested"], json!({"password": "[REDACTED]", "path": "a.txt"}));
        assert_eq!(out["list"], json!(["ok", "token=[REDACTED]"]));
        for text in [out.to_string(), shown(&input)] {
            for secret in ["abc123", "sk-live-1", "fixture-password", "fixture-token"] {
                assert!(!text.contains(secret), "credential escaped into shown/stored tool input");
            }
            assert!(text.contains("https://x") && text.contains("a.txt"));
        }
        assert_eq!(shown(&json!("git status --short")), "git status --short");
        assert_eq!(shown(&json!("echo secret=fixture-inline")), "echo secret=[REDACTED]");
        assert!(input["command"].as_str().unwrap().contains("abc123"), "display redaction must not alter executed input");
    }

    #[test]
    fn secret_object_keys_are_hidden_in_driver_output() {
        let input = json!({"gho_abcdefgh": {"ghu_abcdefgh": "safe"}, "normal": ["ghs_abcdefgh", "ghr_abcdefgh"]});
        assert_eq!(redacted(&input), json!({"[REDACTED]": {"[REDACTED]": "safe"}, "normal": ["[REDACTED]", "[REDACTED]"]}));
        for text in [redacted(&input).to_string(), shown(&input)] {
            assert!(!text.contains("abcdefgh"));
            assert!(text.contains("safe") && text.contains("normal"));
        }
        assert_eq!(redacted(&json!({"ghp_abcdefgh":"last", "gho_abcdefgh":"first"})), json!({"[REDACTED]":"last"}));
    }

    #[test]
    fn the_method_excerpt_stays_short() {
        assert!(METHOD_EXCERPT.chars().count() < 1500);
    }
}
