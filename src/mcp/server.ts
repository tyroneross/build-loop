#!/usr/bin/env node
/**
 * Debugger MCP Server
 *
 * JSON-RPC 2.0 over stdio (MCP protocol).
 * Exposes debugging memory tools: search, store, detail, status, list, patterns, outcome, read_logs.
 */

import { createInterface } from 'readline';
import { TOOLS, handleToolCall } from './tools';
import { configureLogger } from '../logger';

// --- JSON-RPC transport over stdio ---

const rl = createInterface({ input: process.stdin, terminal: false });
let buffer = '';

rl.on('line', (line) => {
  buffer += line;
  try {
    const msg = JSON.parse(buffer);
    buffer = '';
    handleMessage(msg);
  } catch {
    // Incomplete JSON, keep buffering
  }
});

function send(msg: unknown) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

function sendResult(id: string | number, result: unknown) {
  send({ jsonrpc: '2.0', id, result });
}

function sendError(id: string | number, code: number, message: string) {
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

async function handleMessage(msg: {
  jsonrpc: string;
  id?: string | number;
  method: string;
  params?: unknown;
}) {
  if (msg.jsonrpc !== '2.0') return;

  const { id, method, params } = msg;

  try {
    switch (method) {
      case 'initialize': {
        sendResult(id!, {
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
        sendResult(id!, { tools: TOOLS });
        break;
      }

      case 'tools/call': {
        const { name, arguments: args } = params as {
          name: string;
          arguments?: Record<string, unknown>;
        };
        const result = await handleToolCall(name, args || {});
        sendResult(id!, result);
        break;
      }

      default: {
        if (id !== undefined) {
          sendError(id, -32601, `Method not found: ${method}`);
        }
      }
    }
  } catch (err) {
    if (id !== undefined) {
      sendError(
        id,
        -32000,
        err instanceof Error ? err.message : 'Internal error'
      );
    }
  }
}

// Initialize internal logger
configureLogger();

// Log to stderr so it doesn't interfere with the protocol
process.stderr.write('Debugger MCP server started\n');
