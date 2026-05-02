#!/usr/bin/env node
"use strict";
/**
 * Debugger MCP Server
 *
 * JSON-RPC 2.0 over stdio (MCP protocol).
 * Exposes debugging memory tools: search, store, detail, status, list, patterns, outcome, read_logs.
 */
Object.defineProperty(exports, "__esModule", { value: true });
const readline_1 = require("readline");
const tools_1 = require("./tools");
const logger_1 = require("../logger");
// --- JSON-RPC transport over stdio ---
const rl = (0, readline_1.createInterface)({ input: process.stdin, terminal: false });
let buffer = '';
rl.on('line', (line) => {
    buffer += line;
    try {
        const msg = JSON.parse(buffer);
        buffer = '';
        handleMessage(msg);
    }
    catch {
        // Incomplete JSON, keep buffering
    }
});
function send(msg) {
    process.stdout.write(JSON.stringify(msg) + '\n');
}
function sendResult(id, result) {
    send({ jsonrpc: '2.0', id, result });
}
function sendError(id, code, message) {
    send({ jsonrpc: '2.0', id, error: { code, message } });
}
// --- MCP Protocol ---
const SERVER_INFO = {
    name: 'debugger',
    version: '1.8.0',
};
const CAPABILITIES = {
    tools: {},
};
// --- Message handler ---
async function handleMessage(msg) {
    if (msg.jsonrpc !== '2.0')
        return;
    const { id, method, params } = msg;
    try {
        switch (method) {
            case 'initialize': {
                sendResult(id, {
                    protocolVersion: '2025-11-25',
                    serverInfo: SERVER_INFO,
                    capabilities: CAPABILITIES,
                });
                break;
            }
            case 'notifications/initialized': {
                // Client acknowledged — no response needed
                break;
            }
            case 'tools/list': {
                sendResult(id, { tools: tools_1.TOOLS });
                break;
            }
            case 'tools/call': {
                const { name, arguments: args } = params;
                const result = await (0, tools_1.handleToolCall)(name, args || {});
                sendResult(id, result);
                break;
            }
            default: {
                if (id !== undefined) {
                    sendError(id, -32601, `Method not found: ${method}`);
                }
            }
        }
    }
    catch (err) {
        if (id !== undefined) {
            sendError(id, -32000, err instanceof Error ? err.message : 'Internal error');
        }
    }
}
// Initialize internal logger
(0, logger_1.configureLogger)();
// Log to stderr so it doesn't interfere with the protocol
process.stderr.write('Debugger MCP server started\n');
//# sourceMappingURL=server.js.map