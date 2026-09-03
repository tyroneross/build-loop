#!/usr/bin/env node
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
"use strict";

const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const MAX_WORKERS = 8;
const MAX_DURATION_SECONDS = 600;
const TERM_GRACE_MS = 1500;
const POLL_MS = 25;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

function fail(message, code = 2) {
  process.stderr.write(`build-loop-load-probe: ${message}\n`);
  process.exit(code);
}

function parseNumber(raw, name, { min, max }) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < min || value > max) {
    fail(`${name} must be between ${min} and ${max}`);
  }
  return value;
}

function parseArgs(argv) {
  const divider = argv.indexOf("--");
  const own = divider === -1 ? argv : argv.slice(0, divider);
  const command = divider === -1 ? [] : argv.slice(divider + 1);
  const result = {
    worker: false,
    guardian: false,
    json: false,
    workers: 1,
    durationSeconds: 30,
    runId: null,
    deadlineEpochMs: null,
    command,
  };
  for (let index = 0; index < own.length; index += 1) {
    const arg = own[index];
    if (arg === "--worker") result.worker = true;
    else if (arg === "--guardian") result.guardian = true;
    else if (arg === "--json") result.json = true;
    else if (arg === "--workers") result.workers = parseNumber(own[++index], "--workers", { min: 1, max: 10000 });
    else if (arg === "--duration-seconds") result.durationSeconds = parseNumber(own[++index], "--duration-seconds", { min: 1, max: MAX_DURATION_SECONDS });
    else if (arg === "--run-id") result.runId = own[++index];
    else if (arg === "--deadline-epoch-ms") result.deadlineEpochMs = parseNumber(own[++index], "--deadline-epoch-ms", { min: 1, max: Number.MAX_SAFE_INTEGER });
    else if (arg === "--help" || arg === "-h") {
      process.stdout.write("Usage: build-loop-load-probe [--workers N] [--duration-seconds N] [--run-id ID] [--json] [-- command args...]\n");
      process.exit(0);
    } else fail(`unknown argument: ${arg}`);
  }
  if ((result.worker || result.guardian) && (!ID_RE.test(result.runId || "") || result.deadlineEpochMs === null)) fail("internal worker requires a run id and deadline");
  if (!result.worker && !result.guardian && result.runId !== null) fail("--run-id is internal; the supervisor generates an opaque id");
  if (result.guardian && result.command.length === 0) fail("guardian requires a command");
  if (divider !== -1 && command.length === 0) fail("-- requires a command");
  return result;
}

function cacheRoot() {
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Caches", "com.rosslabs.build-loop", "processes");
  }
  return path.join(os.tmpdir(), `build-loop-${process.getuid?.() ?? "user"}`, "processes");
}

function atomicWriteJson(file, value, { exclusive = false } = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  if (exclusive) {
    const descriptor = fs.openSync(file, "wx", 0o600);
    try { fs.writeFileSync(descriptor, `${JSON.stringify(value, null, 2)}\n`); }
    finally { fs.closeSync(descriptor); }
    return;
  }
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

function pruneReceipts(root, retentionDays = 7) {
  const cutoff = Date.now() - retentionDays * 24 * 60 * 60 * 1000;
  try {
    for (const name of fs.readdirSync(root)) {
      if (!/^[0-9a-f-]{36}\.json$/.test(name)) continue;
      const file = path.join(root, name);
      if (fs.statSync(file).mtimeMs < cutoff) fs.unlinkSync(file);
    }
  } catch (error) {
    if (error.code !== "ENOENT") process.stderr.write(`build-loop-load-probe: receipt pruning skipped: ${error.message}\n`);
  }
}

function processIdentity(pid) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", "lstart=", "-o", "pgid=", "-o", "stat=", "-o", "command="], { encoding: "utf8" });
  if (result.status !== 0 || !result.stdout.trim()) return null;
  const line = result.stdout.trim();
  const match = line.match(/^(.{24})\s+(\d+)\s+(\S+)\s+(.+)$/);
  if (!match) return null;
  return { startedAtOs: match[1].trim(), pgid: Number(match[2]), state: match[3], command: match[4] };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function workerMain(options) {
  process.title = `bl-load-w-${options.runId.slice(-6)}`;
  let accumulator = 0;
  while (Date.now() < options.deadlineEpochMs) {
    for (let index = 1; index <= 50000; index += 1) accumulator += Math.sqrt(index + accumulator % 17);
    if (!Number.isFinite(accumulator)) accumulator = 0;
  }
  process.exit(0);
}

async function waitForExit(children, deadlineMs) {
  while (Date.now() < deadlineMs) {
    if (children.every((entry) => entry.child.exitCode !== null || entry.child.signalCode !== null)) return;
    await sleep(POLL_MS);
  }
}

function stillOwned(entry) {
  const current = processIdentity(entry.pid);
  if (!current) return { owned: false, gone: true };
  if (current.state.startsWith("Z")) return { owned: false, gone: true };
  return {
    owned: current.startedAtOs === entry.startedAtOs && current.pgid === entry.pgid && (current.command === entry.title || current.command === entry.initialCommand),
    gone: false,
    current,
  };
}

function processGroupPids(pgid) {
  const result = spawnSync("pgrep", ["-g", String(pgid)], { encoding: "utf8" });
  if (result.status !== 0 && result.status !== 1) throw new Error(`could not inspect process group ${pgid}`);
  return result.stdout.split(/\s+/).filter(Boolean).map(Number).filter((pid) => pid !== process.pid);
}

function pidStillInGroup(pid, pgid) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", "pgid="], { encoding: "utf8" });
  return result.status === 0 && Number(result.stdout.trim()) === pgid;
}

async function guardianMain(options) {
  process.title = `bl-load-t-${options.runId.slice(-6)}`;
  const pgid = process.pid;
  let cleanupPromise = null;
  let requestedSignal = null;
  const target = spawn(options.command[0], options.command.slice(1), { stdio: "inherit" });

  function cleanupGroup() {
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      let members = processGroupPids(pgid);
      for (const pid of members) {
        if (pidStillInGroup(pid, pgid)) {
          try { process.kill(pid, "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") throw error; }
        }
      }
      const termDeadline = Date.now() + TERM_GRACE_MS;
      while (Date.now() < termDeadline && (members = processGroupPids(pgid)).length) await sleep(POLL_MS);
      members = processGroupPids(pgid);
      for (const pid of members) {
        if (pidStillInGroup(pid, pgid)) {
          try { process.kill(pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
        }
      }
      const killDeadline = Date.now() + TERM_GRACE_MS;
      while (Date.now() < killDeadline && processGroupPids(pgid).length) await sleep(POLL_MS);
      const survivors = processGroupPids(pgid);
      if (survivors.length) throw new Error(`target descendants remain: ${survivors.join(",")}`);
    })();
    return cleanupPromise;
  }

  for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    process.once(signal, async () => {
      requestedSignal = signal;
      try { await cleanupGroup(); process.exit(128 + (signal === "SIGTERM" ? 15 : signal === "SIGINT" ? 2 : 1)); }
      catch (error) { process.stderr.write(`build-loop-load-probe guardian: ${error.message}\n`); process.exit(1); }
    });
  }
  const parentWatch = setInterval(async () => {
    if (process.ppid !== 1) return;
    clearInterval(parentWatch);
    try { await cleanupGroup(); process.exit(1); }
    catch (error) { process.stderr.write(`build-loop-load-probe guardian: ${error.message}\n`); process.exit(1); }
  }, 250);

  const targetExit = await new Promise((resolve) => {
    target.once("error", () => resolve(127));
    target.once("exit", (code, signal) => resolve(code ?? (signal ? 128 : 1)));
  });
  clearInterval(parentWatch);
  if (!requestedSignal) {
    try { await cleanupGroup(); process.exit(targetExit); }
    catch (error) { process.stderr.write(`build-loop-load-probe guardian: ${error.message}\n`); process.exit(1); }
  }
}

async function supervisorMain(options) {
  if (process.platform === "win32") fail("synthetic load supervision currently supports macOS and Linux only");
  options.runId = randomUUID();
  process.title = `bl-load-s-${options.runId.slice(-6)}`;
  const cpuCount = Math.max(1, os.cpus().length);
  const cpuCap = Math.max(1, cpuCount - 2);
  const workerCount = Math.min(Math.floor(options.workers), cpuCap, MAX_WORKERS);
  const deadlineEpochMs = Date.now() + Math.round(options.durationSeconds * 1000);
  const receiptRoot = cacheRoot();
  pruneReceipts(receiptRoot);
  const receiptPath = path.join(receiptRoot, `${options.runId}.json`);
  const children = [];
  let target = null;
  let cleanupPromise = null;
  let cleanupErrors = [];
  const receipt = {
    schemaVersion: 1,
    product: "build-loop",
    purpose: "bounded-synthetic-cpu-load",
    runId: options.runId,
    state: "starting",
    supervisor: { pid: process.pid, title: process.title },
    requestedWorkers: Math.floor(options.workers),
    admittedWorkers: workerCount,
    startedAt: new Date().toISOString(),
    deadlineAt: new Date(deadlineEpochMs).toISOString(),
    workers: [],
  };
  atomicWriteJson(receiptPath, receipt, { exclusive: true });

  function cleanup(reason) {
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      receipt.state = "stopping";
      receipt.stopReason = reason;
      atomicWriteJson(receiptPath, receipt);

      // At the hard deadline, let workers finish their current bounded math
      // chunk and deliver their exit event before attempting a signal. This
      // avoids treating the brief running-to-zombie transition as ambiguity.
      if (Date.now() >= deadlineEpochMs) {
        await waitForExit(children, Date.now() + 250);
      }

      for (const entry of children) {
        if (entry.child.exitCode !== null || entry.child.signalCode !== null) continue;
        const check = stillOwned(entry);
        if (!check.gone && !check.owned) {
          cleanupErrors.push(`refused TERM for ambiguous pid ${entry.pid}`);
          continue;
        }
        if (check.owned) {
          try { entry.child.kill("SIGTERM"); } catch (error) {
            if (error.code !== "ESRCH") cleanupErrors.push(`TERM pid ${entry.pid}: ${error.message}`);
          }
        }
      }
      await waitForExit(children, Date.now() + TERM_GRACE_MS);
      const survivors = children.filter((entry) => {
        const check = stillOwned(entry);
        return !check.gone && check.owned;
      }).map((entry) => entry.pid);
      if (survivors.length) cleanupErrors.push(`owned workers remain until hard deadline: ${survivors.join(",")}`);
      receipt.state = cleanupErrors.length ? "cleanup-failed" : "ended";
      receipt.endedAt = new Date().toISOString();
      receipt.cleanup = { verifiedZeroSurvivors: survivors.length === 0, errors: cleanupErrors };
      atomicWriteJson(receiptPath, receipt);
    })();
    return cleanupPromise;
  }

  for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    process.once(signal, async () => {
      if (target && target.exitCode === null && target.signalCode === null) {
        try { target.kill(signal); } catch (error) {
          if (error.code !== "ESRCH") cleanupErrors.push(`target ${signal}: ${error.message}`);
        }
      }
      await cleanup(`supervisor-${signal.toLowerCase()}`);
      process.exit(cleanupErrors.length ? 1 : 128 + (signal === "SIGTERM" ? 15 : signal === "SIGINT" ? 2 : 1));
    });
  }

  try {
    for (let index = 0; index < workerCount; index += 1) {
      const child = spawn(process.execPath, [__filename, "--worker", "--run-id", options.runId, "--deadline-epoch-ms", String(deadlineEpochMs)], {
        detached: true,
        stdio: "ignore",
      });
      let identity = null;
      for (let attempt = 0; attempt < 10 && !identity; attempt += 1) {
        identity = processIdentity(child.pid);
        if (!identity) await sleep(10);
      }
      if (!identity) {
        child.kill("SIGTERM");
        throw new Error(`could not establish identity for worker pid ${child.pid}`);
      }
      const title = `bl-load-w-${options.runId.slice(-6)}`;
      const entry = { child, pid: child.pid, pgid: identity.pgid, startedAtOs: identity.startedAtOs, initialCommand: identity.command, title };
      children.push(entry);
      try {
        os.setPriority(child.pid, os.constants.priority.PRIORITY_BELOW_NORMAL);
      } catch (error) {
        throw new Error(`could not lower priority for worker pid ${child.pid}: ${error.message}`);
      }
      receipt.workers.push({ pid: entry.pid, pgid: entry.pgid, startedAtOs: entry.startedAtOs, title, priority: "below-normal" });
    }
    receipt.state = "running";
    atomicWriteJson(receiptPath, receipt);

    let commandExit = 0;
    if (options.command.length) {
      target = spawn(process.execPath, [__filename, "--guardian", "--run-id", options.runId, "--deadline-epoch-ms", String(deadlineEpochMs), "--", ...options.command], { stdio: "inherit", detached: true });
      commandExit = await new Promise((resolve) => {
        target.once("error", () => resolve(127));
        target.once("exit", (code, signal) => resolve(code ?? (signal ? 128 : 1)));
      });
    } else {
      await sleep(Math.max(0, deadlineEpochMs - Date.now()));
    }
    await cleanup(options.command.length ? "command-exit" : "deadline");
    const output = { runId: options.runId, requestedWorkers: Math.floor(options.workers), admittedWorkers: workerCount, commandExit, receiptId: options.runId, cleanup: receipt.cleanup };
    if (options.json) process.stdout.write(`${JSON.stringify(output)}\n`);
    else process.stdout.write(`build-loop load probe ${options.runId}: ${workerCount} worker(s), zero survivors verified\n`);
    process.exitCode = cleanupErrors.length ? 1 : commandExit;
  } catch (error) {
    cleanupErrors.push(error.message);
    await cleanup("supervisor-error");
    process.stderr.write(`build-loop-load-probe: ${error.message}\n`);
    process.exitCode = 1;
  }
}

const options = parseArgs(process.argv.slice(2));
if (options.worker) workerMain(options);
else if (options.guardian) guardianMain(options);
else supervisorMain(options);
