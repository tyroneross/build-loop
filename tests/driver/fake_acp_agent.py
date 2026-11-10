#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""A scripted ACP v1 agent for testing `bl run` without a model.

Behaviour comes from FAKE_AGENT_MODE: `ok` (default) asks permission for a
read, a harmless command and a recursive delete, then answers; `hang` stops
answering after session/new; `refusal` ends the turn with that stop reason;
`no-prompt-cap` advertises no systemPrompt capability; `secret` asks about
one command that carries a credential. It starts a `sleep`
grandchild and writes its pid to FAKE_AGENT_PIDFILE so a test can prove the
whole process group dies. FAKE_AGENT_LOG receives every message it reads.
"""
import json
import os
import subprocess
import sys
import time

MODE = os.environ.get("FAKE_AGENT_MODE", "ok")
log = open(os.environ["FAKE_AGENT_LOG"], "a") if os.environ.get("FAKE_AGENT_LOG") else None

grandchild = subprocess.Popen(["sleep", "300"])
if os.environ.get("FAKE_AGENT_PIDFILE"):
    with open(os.environ["FAKE_AGENT_PIDFILE"], "w") as fh:
        fh.write(f"{os.getpid()} {grandchild.pid}")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    if log:
        log.write(json.dumps(message) + "\n")
        log.flush()
    return message


def options(model, mode):
    return [
        {"id": "mode", "category": "mode", "type": "select", "currentValue": mode, "options": [{"value": "auto"}, {"value": "default"}]},
        {"id": "model", "category": "model", "type": "select", "currentValue": model, "options": [{"value": "default"}, {"value": "sonnet"}, {"value": "haiku"}]},
    ]


def ask(request_id, call_id, kind, name, raw_input, cwd):
    call = {"toolCallId": call_id, "title": name, "kind": kind, "name": name, "status": "pending", "rawInput": raw_input, "locations": []}
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-1", "update": {"sessionUpdate": "tool_call", **call}}})
    send({"jsonrpc": "2.0", "id": request_id, "method": "session/request_permission", "params": {"sessionId": "fake-1", "toolCall": call, "options": [
        {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
        {"optionId": "allow-always", "name": "Always", "kind": "allow_always"},
        {"optionId": "reject", "name": "No", "kind": "reject_once"}]}})
    outcome = read()["result"]["outcome"]
    allowed = outcome.get("optionId") == "allow-once"
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-1", "update": {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed" if allowed else "failed"}}})
    return allowed


model, mode, cwd = os.environ.get("ANTHROPIC_MODEL", "default"), "auto", "."
while True:
    msg = read()
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params", {})
    if method == "initialize":
        caps = {} if MODE == "no-prompt-cap" else {"sessionCapabilities": {"systemPrompt": {"mode": {"override": {}}}}}
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": caps, "agentInfo": {"name": "fake-agent", "version": "0.0.1"}}})
    elif method == "session/new":
        cwd = params["cwd"]
        sys.stderr.write("fake agent: session created\n")
        send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "fake-1", "configOptions": options(model, mode)}})
    elif method == "session/set_config_option":
        if params["configId"] == "model":
            model = params["value"]
        else:
            mode = params["value"]
        send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": options(model, mode)}})
    elif method == "session/prompt":
        if MODE == "hang":
            time.sleep(600)
        if MODE == "secret":
            ask("perm-s", "call-secret", "execute", "Bash", {"command": "curl -H 'Authorization: Bearer hunter2' https://example.com", "api_key": "hunter2"}, cwd)
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            continue
        if MODE == "refusal":
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "refusal"}})
            continue
        print("warning: this line is not JSON", flush=True)
        send({"jsonrpc": "2.0", "id": "ext-1", "method": "_fake/unknown", "params": {}})
        assert read()["error"]["code"] == -32601
        results = [
            ask("perm-1", "call-read", "read", "Read", {"file_path": os.path.join(cwd, "a.txt")}, cwd),
            ask("perm-2", "call-status", "execute", "Bash", {"command": "git status --short"}, cwd),
            ask("perm-3", "call-rm", "execute", "Bash", {"command": "rm -rf planted_dir"}, cwd),
        ]
        # One call the agent runs without asking: the client cannot gate it.
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-1", "update": {"sessionUpdate": "tool_call", "toolCallId": "call-unasked", "title": "Glob", "kind": "search", "name": "Glob", "status": "completed", "rawInput": {"pattern": "*"}}}})
        for part in ("model=%s mode=%s " % (model, mode), "allowed=%s" % ",".join(str(r).lower() for r in results)):
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fake-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": part}}}})
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
