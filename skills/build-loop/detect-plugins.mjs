#!/usr/bin/env node
/**
 * detect-plugins.mjs — Detect which build-loop–adjacent plugins/skills are installed.
 *
 * Reads ~/.claude/plugins/installed_plugins.json and file-stats personal skills.
 * Emits a single JSON object on stdout. Consumed by build-orchestrator in Phase 1 ASSESS
 * and written to .build-loop/state.json under `availablePlugins`.
 *
 * Zero dependencies. Never throws — returns all-false on any I/O error.
 */

import { readFileSync, existsSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { homedir } from "node:os";

const HOME = homedir();
const REGISTRY = resolve(HOME, ".claude/plugins/installed_plugins.json");

// Plugins we route to. Key = output flag, value = plugin-name prefix in the registry.
// `ibr` is the canonical name for Interface Built Right.
const PLUGIN_MAP = {
  ibr: "ibr",
  showcase: "showcase",
  scraperApp: "scraper-app",
  agentBuilder: "agent-builder",
  claudeCodeDebugger: "claude-code-debugger",
  pyramidPrinciple: "pyramid-principle",
  pluginDev: "plugin-dev",
  replitMigrate: "replit-migrate",
  promptBuilder: "prompt-builder",
};

// Personal skills detected via file-exists (not installed as plugins).
const PERSONAL_SKILLS = {
  appleDev: resolve(HOME, ".claude/skills/apple-dev/SKILL.md"),
};

function loadRegistry() {
  try {
    if (!existsSync(REGISTRY)) return { plugins: {} };
    return JSON.parse(readFileSync(REGISTRY, "utf-8"));
  } catch {
    return { plugins: {} };
  }
}

function findInstall(registry, prefix) {
  const entries = Object.entries(registry.plugins || {})
    .filter(([key]) => key === prefix || key.startsWith(prefix + "@"))
    .flatMap(([, v]) => (Array.isArray(v) ? v : []));
  if (entries.length === 0) return null;
  const sorted = entries
    .slice()
    .sort((a, b) => (b.version || "").localeCompare(a.version || ""));
  return sorted[0];
}

function fileExists(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

const registry = loadRegistry();
const result = { installPaths: {} };

for (const [flag, prefix] of Object.entries(PLUGIN_MAP)) {
  const install = findInstall(registry, prefix);
  result[flag] = !!install;
  if (install?.installPath) result.installPaths[flag] = install.installPath;
}

for (const [flag, path] of Object.entries(PERSONAL_SKILLS)) {
  result[flag] = fileExists(path);
  if (result[flag]) result.installPaths[flag] = path;
}

process.stdout.write(JSON.stringify(result, null, 2) + "\n");
