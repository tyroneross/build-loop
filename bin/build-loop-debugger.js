#!/usr/bin/env node
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const CORE_VERSION = "1.9.0";

function usage() {
  return [
    "Usage:",
    "  build-loop-debugger search <symptom> [--threshold 0.6] [--workdir PATH]",
    "  build-loop-debugger store --input FILE [--workdir PATH]",
    "  build-loop-debugger detail <INC_ID> [--workdir PATH]",
    "  build-loop-debugger status [--workdir PATH]",
  ].join("\n");
}

function parseArgs(argv) {
  const command = argv.shift();
  const positional = [];
  const options = { threshold: 0.5, workdir: process.cwd(), input: null };
  while (argv.length) {
    const arg = argv.shift();
    if (arg === "--threshold") options.threshold = Number(argv.shift());
    else if (arg === "--workdir") options.workdir = path.resolve(argv.shift());
    else if (arg === "--input") options.input = argv.shift();
    else if (arg.startsWith("--")) throw new Error(`unknown option: ${arg}`);
    else positional.push(arg);
  }
  if (!command) throw new Error(usage());
  if (!Number.isFinite(options.threshold) || options.threshold < 0 || options.threshold > 1) {
    throw new Error("--threshold must be between 0 and 1");
  }
  return { command, positional, options };
}

function normalizeIncident(payload, api) {
  if (!payload.symptom || !payload.root_cause || !payload.fix) {
    throw new Error("store input requires symptom, root_cause, and fix");
  }
  const files = Array.isArray(payload.files_changed) ? payload.files_changed : [];
  const rootCause = typeof payload.root_cause === "string"
    ? { description: payload.root_cause, category: payload.category || "unknown", confidence: payload.confidence ?? 0.8 }
    : {
        ...payload.root_cause,
        category: payload.root_cause.category || payload.category || "unknown",
        confidence: payload.root_cause.confidence ?? payload.confidence ?? 0.8,
      };
  const fix = typeof payload.fix === "string"
    ? {
        approach: payload.fix,
        changes: files.map((file) => ({ file, lines_changed: 0, change_type: "modify", summary: payload.fix })),
      }
    : payload.fix;
  const verification = typeof payload.verification === "string"
    ? {
        status: payload.verification === "verified" ? "verified" : "unverified",
        regression_tests_passed: payload.verification === "verified",
        user_journey_tested: false,
        success_criteria_met: payload.verification === "verified",
      }
    : (payload.verification || {
        status: "unverified",
        regression_tests_passed: false,
        user_journey_tested: false,
        success_criteria_met: false,
      });

  return {
    incident_id: api.generateIncidentId(rootCause.category),
    timestamp: Date.now(),
    symptom: payload.symptom,
    session_id: `BUILD_LOOP_${Date.now()}`,
    root_cause: rootCause,
    fix,
    verification,
    tags: Array.isArray(payload.tags) ? payload.tags : ["build-loop"],
    files_changed: files,
    agent_used: payload.agent_used || "build-loop",
    quality_gates: payload.quality_gates || {
      guardian_validated: false,
      tested_e2e: false,
      tested_from_ui: false,
      security_reviewed: false,
      architect_reviewed: false,
    },
  };
}

async function main() {
  const { command, positional, options } = parseArgs(process.argv.slice(2));
  process.chdir(options.workdir);
  // Plugin caches do not ship node_modules. Load only dependency-free debugger
  // modules instead of the package index, which also exports interactive
  // helpers backed by the optional `prompts` package.
  const retrieval = require(path.resolve(__dirname, "../dist/src/retrieval.js"));
  const storage = require(path.resolve(__dirname, "../dist/src/storage.js"));
  const config = require(path.resolve(__dirname, "../dist/src/config.js"));
  const api = { ...retrieval, ...storage };
  const memoryRoot = config.getMemoryPaths().root;
  const originalLog = console.log;
  console.log = (...items) => console.error(...items);
  try {
    if (command === "search") {
      const symptom = positional.join(" ").trim();
      if (!symptom) throw new Error("search requires a symptom");
      const verdict = await api.checkMemoryWithVerdict(symptom, { similarity_threshold: options.threshold });
      return { ok: true, command, debugger_core_version: CORE_VERSION, memory_root: memoryRoot, verdict };
    }
    if (command === "store") {
      if (!options.input) throw new Error("store requires --input FILE");
      const input = options.input === "-" ? fs.readFileSync(0, "utf8") : fs.readFileSync(path.resolve(options.input), "utf8");
      const incident = normalizeIncident(JSON.parse(input), api);
      const stored = await api.storeIncident(incident, { validate_schema: true });
      return { ok: true, command, debugger_core_version: CORE_VERSION, memory_root: memoryRoot, incident_id: stored.incident_id, file_path: stored.file_path };
    }
    if (command === "detail") {
      const id = positional[0];
      if (!id) throw new Error("detail requires an incident ID");
      const incident = await api.loadIncident(id);
      if (!incident) throw new Error(`incident not found: ${id}`);
      return { ok: true, command, debugger_core_version: CORE_VERSION, memory_root: memoryRoot, incident };
    }
    if (command === "status") {
      const status = await api.getMemoryStats();
      return { ok: true, command, debugger_core_version: CORE_VERSION, memory_root: memoryRoot, status };
    }
    throw new Error(`unknown command: ${command}\n${usage()}`);
  } finally {
    console.log = originalLog;
  }
}

main()
  .then((payload) => process.stdout.write(`${JSON.stringify(payload)}\n`))
  .catch((error) => {
    process.stdout.write(`${JSON.stringify({ ok: false, error: error.message })}\n`);
    process.exitCode = 1;
  });
