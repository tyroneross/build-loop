// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! JSON-RPC 2.0 over newline-delimited UTF-8, as the ACP stdio transport
//! defines it (<https://agentclientprotocol.com/protocol/v1/transports>):
//! one message per line, no embedded newlines.
//!
//! One thread, blocking I/O: [`Conn::call`] writes a request and reads until
//! its response arrives, handing every notification and agent-to-client
//! request that arrives meanwhile to the [`Handler`]. A request the handler
//! does not implement is answered `-32601` at once, because an unanswered
//! blocking request hangs the agent. A line that is not JSON, or is longer
//! than [`MAX_LINE`], is dropped and counted; it never ends the session.

use serde_json::{json, Value};
use std::io::{BufRead, Write};

/// Longest accepted line. A tool result can carry a whole file.
pub const MAX_LINE: usize = 8 * 1024 * 1024;

pub const METHOD_NOT_FOUND: i64 = -32601;

#[derive(Debug, Clone, PartialEq)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
}

impl RpcError {
    pub fn method_not_found(method: &str) -> Self {
        RpcError { code: METHOD_NOT_FOUND, message: format!("method not found: {method}") }
    }
}

#[derive(Debug)]
pub enum Error {
    /// The agent closed its stdout.
    Closed,
    Io(std::io::Error),
    /// The agent answered the request with a JSON-RPC error.
    Rpc(RpcError),
    /// A well-formed response that lacks what the protocol requires.
    Protocol(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Closed => write!(f, "agent closed the connection"),
            Error::Io(e) => write!(f, "i/o: {e}"),
            Error::Rpc(e) => write!(f, "agent error {}: {}", e.code, e.message),
            Error::Protocol(m) => write!(f, "protocol: {m}"),
        }
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

#[derive(Debug, PartialEq)]
pub enum Message {
    Response { id: Value, result: Result<Value, RpcError> },
    Request { id: Value, method: String, params: Value },
    Notification { method: String, params: Value },
}

/// Classify one line. `None` when it is not a JSON-RPC message.
pub fn parse(line: &str) -> Option<Message> {
    let Value::Object(mut obj) = serde_json::from_str::<Value>(line).ok()? else {
        return None;
    };
    let id = obj.remove("id").filter(|v| !v.is_null());
    let params = obj.remove("params").unwrap_or(Value::Null);
    if let Some(Value::String(method)) = obj.remove("method") {
        return Some(match id {
            Some(id) => Message::Request { id, method, params },
            None => Message::Notification { method, params },
        });
    }
    let id = id?;
    if let Some(Value::Object(err)) = obj.remove("error") {
        let code = err.get("code").and_then(Value::as_i64).unwrap_or(0);
        let message = err.get("message").and_then(Value::as_str).unwrap_or("").to_string();
        return Some(Message::Response { id, result: Err(RpcError { code, message }) });
    }
    Some(Message::Response { id, result: Ok(obj.remove("result").unwrap_or(Value::Null)) })
}

/// What the client does with traffic the agent starts.
pub trait Handler {
    fn notification(&mut self, _method: &str, _params: &Value) {}
    fn request(&mut self, method: &str, _params: &Value) -> Result<Value, RpcError> {
        Err(RpcError::method_not_found(method))
    }
}

/// A handler that ignores notifications and refuses every request.
pub struct Refuse;
impl Handler for Refuse {}

pub struct Conn<R, W> {
    reader: R,
    writer: W,
    next_id: u64,
    /// Lines dropped because they were not JSON-RPC or exceeded [`MAX_LINE`].
    pub dropped_lines: u64,
}

impl<R: BufRead, W: Write> Conn<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Conn { reader, writer, next_id: 0, dropped_lines: 0 }
    }

    fn send(&mut self, message: &Value) -> Result<(), Error> {
        // serde_json escapes control characters, so the line has no raw newline.
        let mut line = serde_json::to_vec(message).map_err(|e| Error::Protocol(e.to_string()))?;
        line.push(b'\n');
        self.writer.write_all(&line)?;
        self.writer.flush()?;
        Ok(())
    }

    /// One line without its newline; `None` at end of input. An over-long
    /// line is consumed to its end and returned as `Some(None)`.
    fn read_line(&mut self) -> Result<Option<Option<Vec<u8>>>, Error> {
        let mut line: Vec<u8> = Vec::new();
        let mut over = false;
        loop {
            let chunk = match self.reader.fill_buf() {
                Ok(c) => c,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e.into()),
            };
            if chunk.is_empty() {
                return Ok(if line.is_empty() && !over { None } else { Some((!over).then_some(line)) });
            }
            let (take, done) = match chunk.iter().position(|b| *b == b'\n') {
                Some(i) => (i, true),
                None => (chunk.len(), false),
            };
            if !over {
                if line.len() + take > MAX_LINE {
                    over = true;
                    line = Vec::new();
                } else {
                    line.extend_from_slice(&chunk[..take]);
                }
            }
            self.reader.consume(take + usize::from(done));
            if done {
                return Ok(Some((!over).then_some(line)));
            }
        }
    }

    /// The next JSON-RPC message; non-messages are dropped and counted.
    pub fn read_message(&mut self) -> Result<Message, Error> {
        loop {
            let Some(line) = self.read_line()? else {
                return Err(Error::Closed);
            };
            let parsed = line.as_deref().and_then(|l| std::str::from_utf8(l).ok()).and_then(|l| {
                let l = l.trim();
                if l.is_empty() {
                    None
                } else {
                    parse(l)
                }
            });
            match parsed {
                Some(m) => return Ok(m),
                None => {
                    if line.as_deref().is_none_or(|l| !l.iter().all(u8::is_ascii_whitespace)) {
                        self.dropped_lines += 1;
                    }
                }
            }
        }
    }

    pub fn notify(&mut self, method: &str, params: Value) -> Result<(), Error> {
        self.send(&json!({"jsonrpc": "2.0", "method": method, "params": params}))
    }

    /// Send a request and return its result, serving the agent meanwhile.
    pub fn call(&mut self, method: &str, params: Value, handler: &mut dyn Handler) -> Result<Value, Error> {
        let id = self.next_id;
        self.next_id += 1;
        self.send(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))?;
        loop {
            match self.read_message()? {
                Message::Response { id: got, result } if got.as_u64() == Some(id) => {
                    return result.map_err(Error::Rpc);
                }
                // A response to nothing we are waiting on: ignore.
                Message::Response { .. } => {}
                Message::Notification { method, params } => handler.notification(&method, &params),
                Message::Request { id, method, params } => {
                    let reply = match handler.request(&method, &params) {
                        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
                        Err(e) => json!({"jsonrpc": "2.0", "id": id, "error": {"code": e.code, "message": e.message}}),
                    };
                    self.send(&reply)?;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_classifies_the_three_shapes() {
        assert_eq!(
            parse(r#"{"jsonrpc":"2.0","id":1,"result":{"a":1}}"#),
            Some(Message::Response { id: json!(1), result: Ok(json!({"a": 1})) })
        );
        assert_eq!(
            parse(r#"{"jsonrpc":"2.0","id":"x","error":{"code":-32602,"message":"bad"}}"#),
            Some(Message::Response { id: json!("x"), result: Err(RpcError { code: -32602, message: "bad".into() }) })
        );
        assert_eq!(
            parse(r#"{"jsonrpc":"2.0","id":7,"method":"session/request_permission","params":{}}"#),
            Some(Message::Request { id: json!(7), method: "session/request_permission".into(), params: json!({}) })
        );
        assert_eq!(
            parse(r#"{"jsonrpc":"2.0","method":"session/update"}"#),
            Some(Message::Notification { method: "session/update".into(), params: Value::Null })
        );
        assert_eq!(parse("[1,2]"), None);
        assert_eq!(parse("node: warning"), None);
        assert_eq!(parse(r#"{"jsonrpc":"2.0"}"#), None);
    }

    #[test]
    fn noise_and_overlong_lines_are_dropped_not_fatal() {
        let mut input = Vec::new();
        input.extend_from_slice(b"Debugger listening\n\n");
        input.extend_from_slice(&vec![b'x'; MAX_LINE + 10]);
        input.extend_from_slice(b"\n{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":true}\n");
        let mut out = Vec::new();
        let mut conn = Conn::new(&input[..], &mut out);
        let got = conn.call("ping", Value::Null, &mut Refuse).unwrap();
        assert_eq!(got, json!(true));
        assert_eq!(conn.dropped_lines, 2);
    }

    #[test]
    fn end_of_input_is_closed() {
        let mut out = Vec::new();
        let mut conn = Conn::new(&b""[..], &mut out);
        assert!(matches!(conn.call("ping", Value::Null, &mut Refuse), Err(Error::Closed)));
    }

    #[test]
    fn unknown_agent_request_gets_method_not_found() {
        let input = b"{\"jsonrpc\":\"2.0\",\"id\":\"q1\",\"method\":\"cursor/ask_question\",\"params\":{}}\n{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":null}\n";
        let mut out = Vec::new();
        Conn::new(&input[..], &mut out).call("x", Value::Null, &mut Refuse).unwrap();
        let lines: Vec<Value> = out.split(|b| *b == b'\n').filter(|l| !l.is_empty()).map(|l| serde_json::from_slice(l).unwrap()).collect();
        assert_eq!(lines[1]["id"], json!("q1"));
        assert_eq!(lines[1]["error"]["code"], json!(METHOD_NOT_FOUND));
    }
}
