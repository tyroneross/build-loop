#!/usr/bin/env node
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");

function usage() {
  return `build-loop-install

Install build-loop from this npm package into local agent caches.

Usage:
  build-loop-install [options]

Options:
  --host <all|claude|codex>   Agent host cache to sync (default: all)
  --project <slug>            Ensure a project memory scaffold
  --memory-dest <path>        Override build-loop memory root
  --skip-memory               Do not bootstrap build-loop memory
  --dry-run                   Show cache sync actions without writing
  --no-verify                 Skip post-sync cache verification
  --allow-non-mac             Suppress the macOS platform warning
  --json                      Emit one machine-readable JSON result
  -h, --help                  Show help
`;
}

function parseArgs(argv) {
  const args = {
    host: "all",
    projects: [],
    memoryDest: null,
    skipMemory: false,
    dryRun: false,
    noVerify: false,
    allowNonMac: false,
    json: false,
    help: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) {
        throw new Error(`${arg} requires a value`);
      }
      return argv[i];
    };

    if (arg === "-h" || arg === "--help") {
      args.help = true;
    } else if (arg === "--host") {
      args.host = next();
    } else if (arg === "--project") {
      args.projects.push(next());
    } else if (arg === "--memory-dest") {
      args.memoryDest = next();
    } else if (arg === "--skip-memory") {
      args.skipMemory = true;
    } else if (arg === "--dry-run") {
      args.dryRun = true;
    } else if (arg === "--no-verify") {
      args.noVerify = true;
    } else if (arg === "--allow-non-mac") {
      args.allowNonMac = true;
    } else if (arg === "--json") {
      args.json = true;
    } else {
      throw new Error(`unknown option: ${arg}`);
    }
  }

  if (!["all", "claude", "codex"].includes(args.host)) {
    throw new Error("--host must be one of: all, claude, codex");
  }

  return args;
}

function findPython() {
  for (const candidate of ["python3", "python"]) {
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8" });
    if (probe.status === 0) {
      return candidate;
    }
  }
  throw new Error("python3 is required to install build-loop");
}

function parseJsonMaybe(text) {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch (_err) {
    return trimmed;
  }
}

function runStep(label, command, commandArgs, options) {
  const result = spawnSync(command, commandArgs, {
    cwd: root,
    encoding: "utf8",
  });

  const step = {
    label,
    command: [command, ...commandArgs],
    exitCode: result.status,
    ok: result.status === 0,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };

  if (options.json) {
    step.parsedStdout = parseJsonMaybe(step.stdout);
    return step;
  }

  process.stdout.write(`\n== ${label} ==\n`);
  if (step.stdout) {
    process.stdout.write(step.stdout);
    if (!step.stdout.endsWith("\n")) process.stdout.write("\n");
  }
  if (step.stderr) {
    process.stderr.write(step.stderr);
    if (!step.stderr.endsWith("\n")) process.stderr.write("\n");
  }
  if (!step.ok) {
    process.stderr.write(`${label} failed with exit ${step.exitCode}\n`);
  }
  return step;
}

function syncArgs({ host, source, args }) {
  const cmd = [
    path.join(root, "scripts", "sync_plugin_cache.py"),
    "--source",
    source,
    "--host",
    host,
    "--marketplace",
    "build-loop",
    "--dirty",
  ];
  if (args.dryRun) cmd.push("--dry-run");
  if (args.noVerify) cmd.push("--no-verify");
  if (args.json) cmd.push("--json");
  return cmd;
}

function memoryArgs(args) {
  const cmd = [path.join(root, "scripts", "install_memory.py")];
  if (args.memoryDest) {
    cmd.push("--dest", args.memoryDest);
  }
  for (const project of args.projects) {
    cmd.push("--ensure-project", project);
  }
  return cmd;
}

function hostsFor(host) {
  return host === "all" ? ["claude", "codex"] : [host];
}

function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err.message}\n\n${usage()}`);
    return 2;
  }

  if (args.help) {
    process.stdout.write(usage());
    return 0;
  }

  const python = findPython();
  const steps = [];
  const codexArtifact = path.join(root, "plugin-artifacts", "codex");

  if (process.platform !== "darwin" && !args.allowNonMac && !args.json) {
    process.stderr.write(
      "warning: this installer is optimized for macOS agent cache paths; continuing on this platform.\n"
    );
  }

  for (const host of hostsFor(args.host)) {
    const source = host === "codex" && fs.existsSync(codexArtifact) ? codexArtifact : root;
    steps.push(
      runStep(`sync ${host} plugin cache`, python, syncArgs({ host, source, args }), args)
    );
  }

  if (!args.skipMemory && !args.dryRun) {
    steps.push(runStep("bootstrap build-loop memory", python, memoryArgs(args), args));
  } else if (args.dryRun && !args.json) {
    process.stdout.write("\n== bootstrap build-loop memory ==\nskipped during --dry-run\n");
  }

  const payload = {
    ok: steps.every((step) => step.ok),
    packageRoot: root,
    platform: process.platform,
    host: args.host,
    memory: args.skipMemory ? "skipped" : args.dryRun ? "dry-run-skipped" : "bootstrapped",
    steps,
  };

  if (args.json) {
    process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  }

  return payload.ok ? 0 : 1;
}

try {
  process.exitCode = main();
} catch (err) {
  process.stderr.write(`${err.message}\n`);
  process.exitCode = 1;
}
