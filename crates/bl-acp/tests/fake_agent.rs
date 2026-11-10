// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The client against a scripted fake agent: a whole session over a socket
//! pair, then a real subprocess to prove the process group is killed.

use bl_acp::agent::{Agent, Launch};
use bl_acp::rpc::{Conn, Error, Handler, RpcError};
use bl_acp::session::{self, NewSession, PermissionRequest};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;

/// Records what the agent sent and allows every permission request.
#[derive(Default)]
struct Recorder {
    text: String,
    permissions: Vec<PermissionRequest>,
}

impl Handler for Recorder {
    fn notification(&mut self, method: &str, params: &Value) {
        if method == "session/update" && params["update"]["sessionUpdate"] == "agent_message_chunk" {
            self.text.push_str(params["update"]["content"]["text"].as_str().unwrap_or(""));
        }
    }
    fn request(&mut self, method: &str, params: &Value) -> Result<Value, RpcError> {
        if method != "session/request_permission" {
            return Err(RpcError::method_not_found(method));
        }
        let request = PermissionRequest::parse(params);
        let reply = request.reply(request.kind == "read");
        self.permissions.push(request);
        Ok(reply)
    }
}

/// The agent side: answers by method, and during the prompt asks permission
/// twice, sends an extension request, noise, and streamed text.
fn fake_agent(stream: UnixStream) -> Vec<Value> {
    let mut seen = Vec::new();
    let out = stream.try_clone().unwrap();
    let mut lines = BufReader::new(stream).lines();
    let send = |v: Value| writeln!(&out, "{v}").unwrap();
    while let Some(Ok(line)) = lines.next() {
        let msg: Value = serde_json::from_str(&line).unwrap();
        seen.push(msg.clone());
        let id = msg["id"].clone();
        match msg["method"].as_str() {
            Some("initialize") => send(json!({"jsonrpc": "2.0", "id": id, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"sessionCapabilities": {"systemPrompt": {"mode": {"override": {}}}}},
                "agentInfo": {"name": "fake", "version": "9.9.9"}}})),
            Some("session/new") => send(json!({"jsonrpc": "2.0", "id": id, "result": {"sessionId": "s1", "configOptions": [
                {"id": "model", "category": "model", "type": "select", "currentValue": "big", "options": [{"value": "big"}, {"value": "small"}]}]}})),
            Some("session/set_config_option") => send(json!({"jsonrpc": "2.0", "id": id, "result": {"configOptions": [
                {"id": "model", "category": "model", "type": "select", "currentValue": msg["params"]["value"], "options": [{"value": "big"}, {"value": "small"}]}]}})),
            Some("session/prompt") => {
                writeln!(&out, "(node:1) Warning: not json").unwrap();
                let options = json!([{"optionId": "yes", "kind": "allow_once"}, {"optionId": "always", "kind": "allow_always"}, {"optionId": "no", "kind": "reject_once"}]);
                for (rid, kind) in [("p1", "read"), ("p2", "execute")] {
                    send(json!({"jsonrpc": "2.0", "id": rid, "method": "session/request_permission", "params": {"sessionId": "s1",
                        "toolCall": {"toolCallId": rid, "title": "t", "kind": kind, "rawInput": {"command": "ls"}, "locations": [{"path": "/p/a"}]}, "options": options}}));
                    seen.push(serde_json::from_str(&lines.next().unwrap().unwrap()).unwrap());
                }
                send(json!({"jsonrpc": "2.0", "id": "x1", "method": "_vendor/ask", "params": {}}));
                seen.push(serde_json::from_str(&lines.next().unwrap().unwrap()).unwrap());
                for part in ["rea", "dy"] {
                    send(json!({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": part}}}}));
                }
                send(json!({"jsonrpc": "2.0", "id": id, "result": {"stopReason": "end_turn"}}));
            }
            _ => send(json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "unknown"}})),
        }
    }
    seen
}

#[test]
fn a_whole_session_against_the_scripted_agent() {
    let (client, agent) = UnixStream::pair().unwrap();
    let agent_thread = std::thread::spawn(move || fake_agent(agent));
    let mut conn = Conn::new(BufReader::new(client.try_clone().unwrap()), client);
    let mut rec = Recorder::default();

    let init = session::initialize(&mut conn, &mut rec, "0.0.0").unwrap();
    assert_eq!((init.agent_name.as_str(), init.agent_version.as_str(), init.system_prompt), ("fake", "9.9.9", true));

    let meta = json!({"vendor": {"x": 1}});
    let mut sess = session::new_session(&mut conn, &mut rec, &NewSession { cwd: "/p", mcp_servers: &json!([]), append_instructions: Some("method"), meta: Some(&meta) }).unwrap();
    assert_eq!(sess.id, "s1");
    assert_eq!(sess.option("model").unwrap().current, "big");
    session::set_config_option(&mut conn, &mut rec, &mut sess, "model", "small").unwrap();
    assert_eq!(sess.option("model").unwrap().current, "small");

    let stop = session::prompt(&mut conn, &mut rec, &sess, "go").unwrap();
    assert_eq!(stop, "end_turn");
    assert_eq!(rec.text, "ready");
    assert_eq!(rec.permissions.len(), 2);
    assert_eq!(rec.permissions[0].locations, vec!["/p/a".to_string()]);
    assert_eq!(conn.dropped_lines, 1);

    assert!(matches!(conn.call("nope", Value::Null, &mut rec), Err(Error::Rpc(e)) if e.code == -32601));
    drop(conn);
    let seen = agent_thread.join().unwrap();
    let new = seen.iter().find(|m| m["method"] == "session/new").unwrap();
    assert_eq!(new["params"]["systemPrompt"], json!({"mode": "append", "content": [{"type": "text", "text": "method"}]}));
    assert_eq!(new["params"]["_meta"], meta);
    assert_eq!(seen[0]["params"]["clientCapabilities"], json!({}));
    let reply = |id: &str| seen.iter().find(|m| m["id"] == id && m.get("method").is_none()).unwrap().clone();
    // Allowed with the one-time option, never "always"; refused with reject_once.
    assert_eq!(reply("p1")["result"]["outcome"], json!({"outcome": "selected", "optionId": "yes"}));
    assert_eq!(reply("p2")["result"]["outcome"], json!({"outcome": "selected", "optionId": "no"}));
    assert_eq!(reply("x1")["error"]["code"], json!(-32601));
}

#[test]
fn a_refusal_without_a_reject_option_cancels() {
    let request = PermissionRequest::parse(&json!({"toolCall": {"toolCallId": "c"}, "options": [{"optionId": "always", "kind": "allow_always"}]}));
    assert_eq!(request.reply(false), json!({"outcome": {"outcome": "cancelled"}}));
    assert_eq!(request.reply(true), json!({"outcome": {"outcome": "cancelled"}}));
}

#[test]
fn another_protocol_version_is_refused() {
    let input = b"{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":{\"protocolVersion\":2}}\n";
    let mut out = Vec::new();
    let got = session::initialize(&mut Conn::new(&input[..], &mut out), &mut Recorder::default(), "0");
    assert!(matches!(got, Err(Error::Protocol(_))));
}

fn alive(pid: &str) -> bool {
    std::process::Command::new("kill").args(["-0", pid]).stderr(std::process::Stdio::null()).status().unwrap().success()
}

#[test]
fn killing_the_agent_kills_what_it_started() {
    // A shell agent that starts a grandchild, reports its pid, then idles.
    let script = "sleep 300 & printf '{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":{\"pid\":%d}}\\n' $!; echo oops >&2; wait";
    let mut agent = Agent::spawn(&Launch { command: "sh".into(), args: vec!["-c".into(), script.into()], env: vec![] }).unwrap();
    let result = agent.conn.call("x", Value::Null, &mut Recorder::default()).unwrap();
    let grandchild = result["pid"].as_u64().unwrap().to_string();
    assert!(alive(&grandchild));
    for _ in 0..100 {
        if agent.stderr_tail().contains("oops") {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    assert_eq!(agent.stderr_tail().trim(), "oops");
    drop(agent);
    for _ in 0..200 {
        if !alive(&grandchild) {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    panic!("grandchild {grandchild} outlived the agent");
}
