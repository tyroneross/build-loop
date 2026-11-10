// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The ACP v1 methods one driven session needs: `initialize`, `session/new`,
//! `session/set_config_option`, `session/prompt`, and the shape of
//! `session/request_permission`.
//!
//! The client advertises no `fs` or `terminal` capability: the v2 draft
//! removes them, so nothing here may depend on them.

use crate::rpc::{Conn, Error, Handler};
use serde_json::{json, Value};
use std::io::{BufRead, Write};

pub const PROTOCOL_VERSION: u64 = 1;

#[derive(Debug, Clone, Default)]
pub struct Initialized {
    pub agent_name: String,
    pub agent_version: String,
    /// `agentCapabilities.sessionCapabilities.systemPrompt` is advertised, so
    /// `systemPrompt` with mode `append` may be sent.
    pub system_prompt: bool,
}

pub fn initialize<R: BufRead, W: Write>(conn: &mut Conn<R, W>, handler: &mut dyn Handler, client_version: &str) -> Result<Initialized, Error> {
    let result = conn.call(
        "initialize",
        json!({
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {},
            "clientInfo": {"name": "bl", "title": "build-loop driver", "version": client_version},
        }),
        handler,
    )?;
    let version = result.get("protocolVersion").and_then(Value::as_u64);
    if version != Some(PROTOCOL_VERSION) {
        return Err(Error::Protocol(format!("agent speaks protocol version {version:?}; this client speaks {PROTOCOL_VERSION}")));
    }
    let info = result.get("agentInfo");
    let text = |key: &str| info.and_then(|i| i.get(key)).and_then(Value::as_str).unwrap_or("").to_string();
    Ok(Initialized {
        agent_name: text("name"),
        agent_version: text("version"),
        system_prompt: result.pointer("/agentCapabilities/sessionCapabilities/systemPrompt").is_some_and(|v| !v.is_null()),
    })
}

/// One session config option (`configOptions[]`), select-typed values only.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigOption {
    pub id: String,
    /// `mode`, `model`, `thought_level`, ... (may be empty).
    pub category: String,
    pub current: String,
    pub values: Vec<String>,
}

fn config_options(result: &Value) -> Vec<ConfigOption> {
    let text = |v: &Value, key: &str| v.get(key).and_then(Value::as_str).unwrap_or("").to_string();
    result
        .get("configOptions")
        .and_then(Value::as_array)
        .map(|options| {
            options
                .iter()
                .map(|o| ConfigOption {
                    id: text(o, "id"),
                    category: text(o, "category"),
                    current: text(o, "currentValue"),
                    values: o
                        .get("options")
                        .and_then(Value::as_array)
                        .map(|vs| vs.iter().filter_map(|v| v.get("value").and_then(Value::as_str).map(str::to_string)).collect())
                        .unwrap_or_default(),
                })
                .collect()
        })
        .unwrap_or_default()
}

#[derive(Debug, Clone)]
pub struct Session {
    pub id: String,
    pub config: Vec<ConfigOption>,
}

impl Session {
    /// The first option of a category (`model`, `mode`).
    pub fn option(&self, category: &str) -> Option<&ConfigOption> {
        self.config.iter().find(|o| o.category == category)
    }
}

pub struct NewSession<'a> {
    /// Absolute path.
    pub cwd: &'a str,
    /// `mcpServers` array, passed through.
    pub mcp_servers: &'a Value,
    /// Sent as `systemPrompt {mode: "append"}`; the caller checks
    /// [`Initialized::system_prompt`] first, as the spec requires.
    pub append_instructions: Option<&'a str>,
    /// Agent-specific `_meta`, passed through from the profile.
    pub meta: Option<&'a Value>,
}

pub fn new_session<R: BufRead, W: Write>(conn: &mut Conn<R, W>, handler: &mut dyn Handler, request: &NewSession) -> Result<Session, Error> {
    let mut params = json!({"cwd": request.cwd, "mcpServers": request.mcp_servers});
    if let Some(text) = request.append_instructions.filter(|t| !t.is_empty()) {
        params["systemPrompt"] = json!({"mode": "append", "content": [{"type": "text", "text": text}]});
    }
    if let Some(meta) = request.meta.filter(|m| !m.is_null()) {
        params["_meta"] = meta.clone();
    }
    let result = conn.call("session/new", params, handler)?;
    let id = result.get("sessionId").and_then(Value::as_str).ok_or_else(|| Error::Protocol("session/new returned no sessionId".into()))?;
    Ok(Session { id: id.to_string(), config: config_options(&result) })
}

/// Set one select option; the agent answers with the complete option list.
pub fn set_config_option<R: BufRead, W: Write>(conn: &mut Conn<R, W>, handler: &mut dyn Handler, session: &mut Session, config_id: &str, value: &str) -> Result<(), Error> {
    let result = conn.call("session/set_config_option", json!({"sessionId": session.id, "configId": config_id, "value": value}), handler)?;
    let updated = config_options(&result);
    if !updated.is_empty() {
        session.config = updated;
    }
    Ok(())
}

/// Run one prompt turn; returns the `stopReason`.
pub fn prompt<R: BufRead, W: Write>(conn: &mut Conn<R, W>, handler: &mut dyn Handler, session: &Session, text: &str) -> Result<String, Error> {
    let result = conn.call("session/prompt", json!({"sessionId": session.id, "prompt": [{"type": "text", "text": text}]}), handler)?;
    Ok(result.get("stopReason").and_then(Value::as_str).unwrap_or("").to_string())
}

/// `session/request_permission` params, reduced to what a policy reads.
#[derive(Debug, Clone, PartialEq)]
pub struct PermissionRequest {
    pub tool_call_id: String,
    pub title: String,
    /// ACP tool kind: read, edit, delete, move, search, execute, think,
    /// fetch, switch_mode, other. Empty when the agent sent none.
    pub kind: String,
    pub raw_input: Value,
    /// `locations[].path`.
    pub locations: Vec<String>,
    /// `(optionId, kind)`; kinds allow_once, allow_always, reject_once,
    /// reject_always.
    pub options: Vec<(String, String)>,
}

impl PermissionRequest {
    pub fn parse(params: &Value) -> PermissionRequest {
        let call = params.get("toolCall").unwrap_or(&Value::Null);
        let text = |v: &Value, key: &str| v.get(key).and_then(Value::as_str).unwrap_or("").to_string();
        PermissionRequest {
            tool_call_id: text(call, "toolCallId"),
            title: text(call, "title"),
            kind: text(call, "kind"),
            raw_input: call.get("rawInput").cloned().unwrap_or(Value::Null),
            locations: call
                .get("locations")
                .and_then(Value::as_array)
                .map(|ls| ls.iter().filter_map(|l| l.get("path").and_then(Value::as_str).map(str::to_string)).collect())
                .unwrap_or_default(),
            options: params
                .get("options")
                .and_then(Value::as_array)
                .map(|os| os.iter().map(|o| (text(o, "optionId"), text(o, "kind"))).collect())
                .unwrap_or_default(),
        }
    }

    /// The response for a decision: the one-time option of that kind, never
    /// an `*_always` option. With no such option the request is `cancelled`,
    /// which the spec defines as not running the tool.
    pub fn reply(&self, allow: bool) -> Value {
        let wanted = if allow { "allow_once" } else { "reject_once" };
        match self.options.iter().find(|(_, kind)| kind == wanted) {
            Some((id, _)) => json!({"outcome": {"outcome": "selected", "optionId": id}}),
            None => json!({"outcome": {"outcome": "cancelled"}}),
        }
    }
}
