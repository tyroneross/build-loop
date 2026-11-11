// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Fresh-cache native Rally restore. Unsupported discovery or identity falls
//! back before the room read; once read, failure is quiet and is never replayed.
use bl_contract::{cache, identity, py, Fallback, R};
use serde_json::Value;
use std::path::Path;
use std::process::Command;
use std::time::Instant;
use crate::pre_edit::{fresh_restore_context, var};
use crate::session_start::{run_bounded, run_room, Ran};

pub(crate) fn restore(root: &str, workdir: &str, tool: &str, session: &str, deadline: &str, until: Instant) -> R<Ran> {
    // backend_adapter.stable_session_id has a separate fallback hash. Keep
    // that uncommon path with Python until it has its own shared contract.
    let stable = [var("BUILD_LOOP_RALLY_SESSION_ID")?, var("RALLY_SESSION_ID")?]
        .into_iter().flatten().find(|s| !py::strip(s).is_empty())
        .ok_or(Fallback("restore session fallback"))?;
    // Python arms its internal alarm before dispatch. Preserve its quiet
    // completion (and background stage) when that alarm precedes the host cut.
    let seconds = py::float(deadline)?.ok_or(Fallback("restore deadline"))?;
    if seconds < 0.0 || !seconds.is_finite() { return Err(Fallback("restore deadline")); }
    let inner = if seconds == 0.0 { until } else {
        until.min(Instant::now() + std::time::Duration::from_secs_f64(seconds.min(10.0)))
    };
    // A smaller probe timeout would be spent again by Python fallback.
    // Leave that budget configuration entirely with Python before any probe.
    let probe_budget = std::time::Duration::from_secs_f64(
        cache::probe_timeout_seconds(var("RALLY_HOOK_BUDGET_MS")?.as_deref())?);
    if probe_budget < inner.saturating_duration_since(Instant::now()) {
        return Err(Fallback("short rally probe budget"));
    }
    // session_start_safe always checks Git, even with CLAUDE_PROJECT_DIR set.
    let mut git = Command::new("git");
    git.args(["-C", workdir, "rev-parse", "--git-dir"]);
    match run_bounded(git, None, inner) {
        Ran::Done { ok: true, out } if !out.iter().all(u8::is_ascii_whitespace) => {},
        Ran::Cut => return Ok(cut(inner, until)),
        _ => return Ok(Ran::Done { out: Vec::new(), ok: true }),
    }
    let actor = actor(tool, session)?;
    let context = fresh_restore_context(root, workdir, |binary| {
        if !binary.contains('/') { return Err(Fallback("bare rally candidate")); }
        if !Path::new(binary).is_file() { return Ok(false); }
        let probe_until = inner.min(Instant::now() + probe_budget);
        match run_bounded(Command::new(binary), None, probe_until) {
            Ran::Done { out, .. } => {
                let text = std::str::from_utf8(&out).map_err(|_| Fallback("rally help encoding"))?;
                // Do not select a later candidate: this one could have
                // printed its valid usage on stderr, which Python accepts.
                if cache::RALLY_HELP_FRAGMENTS.iter().all(|fragment| text.contains(fragment)) {
                    Ok(true)
                } else { Err(Fallback("rally usage requires Python probe")) }
            }
            Ran::Cut => Err(Fallback("rally probe deadline")),
        }
    });
    if Instant::now() >= inner { return Ok(cut(inner, until)); }
    let (dir, binary, slug) = context?;
    let mut command = Command::new(binary);
    command.args(["room", "--json", "--readers"]).current_dir(dir)
        .env("RALLY_SESSION_ID", py::strip(&stable));
    if var("RALLY_OBSERVER_PID")?.is_none() {
        command.env("RALLY_OBSERVER_PID", std::process::id().to_string());
    }
    Ok(match run_room(command, inner) {
        Ran::Done { out, ok } => Ran::Done { out: if ok { render(&out, &actor, &slug) } else { Vec::new() }, ok: true },
        Ran::Cut => cut(inner, until),
    })
}

fn cut(inner: Instant, outer: Instant) -> Ran {
    if inner < outer { Ran::Done { out: Vec::new(), ok: true } } else { Ran::Cut }
}

fn actor(tool: &str, session: &str) -> R<String> {
    if let Some(value) = var("RALLY_TOOL_ID")? {
        if !py::strip(&value).is_empty() { return Ok(py::strip(&value).to_string()); }
    }
    let family = identity::base_tool(tool);
    let budget = 64 - family.len() - 1;
    let explicit = py::strip(tool).split_once(':').map(|(_, actor)| actor).filter(|s| !py::strip(s).is_empty());
    let session = identity::resolve_session_id(&family, Some(session), &var)?;
    Ok(format!("{family}:{}", identity::safe_segment(explicit.unwrap_or(&session), budget)))
}

fn unknown(value: &Value) -> bool {
    match value {
        Value::Object(obj) => obj.iter().any(|(key, value)| (key == "code" && value == "outcome_unknown") || unknown(value)),
        Value::Array(items) => items.iter().any(unknown),
        _ => false,
    }
}

fn render(bytes: &[u8], actor: &str, slug: &str) -> Vec<u8> {
    let Ok(value) = serde_json::from_slice::<Value>(bytes) else { return Vec::new(); };
    if value["ok"] != true || value["product"] != "rally" || value["schema"] != "agent-rally.command.room.v1"
        || value["command"] == "mutation_outcome_unknown" || py::truthy(&value["data"]["outcome_unknown"]) || unknown(&value) {
        return Vec::new();
    }
    let outer = &value["data"]["room"];
    let room = if outer["room"].is_object() { &outer["room"] } else { outer };
    let count = room["squads"].as_array().map(|items| items.iter().filter(|peer|
        peer.is_object() && peer["tool"] != actor && peer["status"] == "active"
    ).count()).unwrap_or(0);
    if count == 0 { return Vec::new(); }
    let message = format!("Rally Point: {slug} - {count} live peer(s)");
    format!("{{\"hookSpecificOutput\": {{\"hookEventName\": \"SessionStart\", \"additionalContext\": {}}}}}\n", py::json_string(&message)).into_bytes()
}
