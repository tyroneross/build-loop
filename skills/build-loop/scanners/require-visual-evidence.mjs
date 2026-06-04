#!/usr/bin/env node
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//
// require-visual-evidence.mjs — BL-1 gate
//
// Rejects UI-touching chunks whose only verification evidence is symbol/string
// presence (nm / strings / grep over compiled binaries / `git grep` over source).
//
// Trigger surfaces (called by build-orchestrator):
//   - Phase 3 chunk-close (commit step) when `uiTouched: true`
//   - Phase 4 sub-step B (Validate) when `uiTarget != null` and any changed
//     file matches the UI-file globs below
//
// Input (--envelope-file <path>): a JSON file with the implementer/chunk
// return envelope, at minimum:
//   {
//     "uiTarget": "macos" | "mobile" | "web" | null,
//     "files_changed": ["...", "..."],
//     "verification": "<freeform string describing how the implementer verified>",
//     "evidence_paths": ["screenshots/foo.png", ...]   // optional
//   }
// Extra fields are ignored. Missing fields are treated conservatively
// (no claim => no evidence).
//
// Exit codes:
//   0  pass            — non-UI diff OR genuine visual/AX evidence present
//   1  warn            — UI diff + ambiguous evidence; agent should clarify
//   2  reject          — UI diff + ONLY symbol/string evidence; BLOCK chunk-close
//   3  malformed       — envelope unreadable / invalid JSON
//
// Always writes a JSON object to stdout:
//   { verdict, reason, ui_changed, ui_files, symbol_only, accepted_evidence }
//
// Stdlib-only. No dependencies. Mirrors the shape of audit-design-rules.mjs.

import fs from 'node:fs';
import path from 'node:path';

// --- UI file detection ------------------------------------------------------

const UI_FILE_PATTERNS = [
  /(^|\/)Views\//,            // Apple-platform convention
  /\.swift$/,                 // any Swift source (heuristic — paired w/ uiTarget)
  /\.tsx$/,
  /\.jsx$/,
  /\.vue$/,
  /\.svelte$/,
  /(^|\/)components?\/.+\.(ts|js)$/,
  /(^|\/)pages?\/.+\.(ts|js)$/,
  /(^|\/)app\/.+\.(ts|js)$/,
];

function isUiFile(p) {
  return UI_FILE_PATTERNS.some((rx) => rx.test(p));
}

// --- Evidence classification ------------------------------------------------

// Accepted visual/AX evidence — any one of these in the verification text
// or as an evidence path is enough to clear the gate.
const ACCEPTED_EVIDENCE_TOKENS = [
  // Screenshot / image artifacts
  /screenshot/i,
  /\.png\b/i,
  /\.jpe?g\b/i,
  // AX tree dumps
  /\bax[-_ ]?tree\b/i,
  /accessibility[-_ ]?tree/i,
  // macOS / iOS native verification
  /native[-_ ]?ax[-_ ]?driver/i,
  /\bxcrun simctl io booted screenshot\b/i,
  /\bidb ui\b/i,
  // IBR + scan
  /\bscan_macos\b/i,
  /\bibr\b[^a-z]*scan/i,
  /ssim/i,
  // Web rendering
  /\bplaywright\b/i,
  /\bpuppeteer\b/i,
  /\bui[-_ ]?validator\b/i,
  /dev[-_ ]?server.*(?:screenshot|render)/i,
  // PID-anchored verification (the running app was actually looked at)
  /\bpid[:= ]\s*\d+/i,
];

// Symbol-only evidence — `nm`, `strings`, `grep` over compiled output, or
// a "the identifier exists therefore the UI is correct" claim. These DO NOT
// satisfy the gate on their own.
const SYMBOL_ONLY_TOKENS = [
  /\bnm\s+[^\n]*\.(?:app|dylib|framework|so|exe|dll|o|a|bundle)/i,
  /\bstrings\s+/i,
  /\botool\s+-[lL]\b/i,
  /\bgit grep\b/i,
  /\bgrep\b.*(?:Sources|Views|src)\//i,
  /\bsymbol(?:s)?\s+(?:present|exist|found)/i,
  /\bidentifier(?:s)?\s+(?:present|exist|found)/i,
  /\bcompiles?\s+(?:cleanly|successfully)/i,   // build-only claim
];

function classifyEvidence(verificationText, evidencePaths) {
  const acceptedHits = [];
  const symbolHits = [];

  const haystacks = [verificationText, ...(evidencePaths || [])];
  for (const text of haystacks) {
    if (typeof text !== 'string') continue;
    for (const rx of ACCEPTED_EVIDENCE_TOKENS) {
      if (rx.test(text)) acceptedHits.push(rx.source);
    }
    for (const rx of SYMBOL_ONLY_TOKENS) {
      if (rx.test(text)) symbolHits.push(rx.source);
    }
  }
  return {
    accepted_evidence: [...new Set(acceptedHits)],
    symbol_signals: [...new Set(symbolHits)],
  };
}

// --- Main -------------------------------------------------------------------

function parseArgs(argv) {
  const args = { envelopeFile: null, root: '.' };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--envelope-file') args.envelopeFile = argv[++i];
    else if (a === '--root') args.root = argv[++i];
    else if (a === '--help' || a === '-h') {
      // Embedded usage so stdlib-only -- no separate help file
      process.stdout.write(
        'Usage: require-visual-evidence.mjs --envelope-file <path> [--root <project-root>]\n'
      );
      process.exit(0);
    }
  }
  return args;
}

function emit(verdict, reason, extras) {
  const out = { verdict, reason, ...extras };
  process.stdout.write(JSON.stringify(out) + '\n');
  if (verdict === 'pass') process.exit(0);
  if (verdict === 'warn') process.exit(1);
  if (verdict === 'reject') process.exit(2);
  process.exit(3);
}

function main() {
  const args = parseArgs(process.argv);
  if (!args.envelopeFile) {
    emit('malformed', '--envelope-file is required', {});
  }

  let raw;
  try {
    raw = fs.readFileSync(args.envelopeFile, 'utf8');
  } catch (err) {
    emit('malformed', `cannot read envelope: ${err.message}`, {});
  }

  let env;
  try {
    env = JSON.parse(raw);
  } catch (err) {
    emit('malformed', `invalid JSON: ${err.message}`, {});
  }

  const uiTarget = env.uiTarget ?? null;
  const filesChanged = Array.isArray(env.files_changed) ? env.files_changed : [];
  const verification = typeof env.verification === 'string' ? env.verification : '';
  const evidencePaths = Array.isArray(env.evidence_paths) ? env.evidence_paths : [];

  // Skip when no UI target is configured for this build.
  if (uiTarget === null || uiTarget === undefined) {
    emit('pass', 'uiTarget is null; gate does not apply', {
      ui_changed: false,
      ui_files: [],
      symbol_only: false,
      accepted_evidence: [],
    });
  }

  // Skip when this chunk didn't touch any UI file.
  const uiFiles = filesChanged.filter(isUiFile);
  if (uiFiles.length === 0) {
    emit('pass', 'no UI files changed in this chunk', {
      ui_changed: false,
      ui_files: [],
      symbol_only: false,
      accepted_evidence: [],
    });
  }

  // UI was touched — evidence is required.
  const { accepted_evidence, symbol_signals } = classifyEvidence(verification, evidencePaths);

  if (accepted_evidence.length > 0) {
    emit('pass', 'visual/AX evidence present', {
      ui_changed: true,
      ui_files: uiFiles,
      symbol_only: false,
      accepted_evidence,
    });
  }

  if (symbol_signals.length > 0) {
    emit(
      'reject',
      'symbol/string-only evidence is NOT a substitute for visual/AX verification. ' +
        'Required: render the running app (pid-anchored) and capture a screenshot, AX-tree dump, ' +
        'or scan result. See skills/build-loop/phases/ui-validation.md §"Visual validation".',
      {
        ui_changed: true,
        ui_files: uiFiles,
        symbol_only: true,
        accepted_evidence: [],
        symbol_signals,
      }
    );
  }

  // No accepted evidence AND no symbol-only signal — verification text is
  // ambiguous (empty, unrelated, or in an unknown shape). Surface a warning
  // and let the orchestrator decide whether to re-prompt the implementer.
  emit(
    'warn',
    'no recognized visual/AX evidence in the envelope and no symbol-only signal either; ' +
      're-prompt the implementer to attach a screenshot, AX-tree dump, or scan result.',
    {
      ui_changed: true,
      ui_files: uiFiles,
      symbol_only: false,
      accepted_evidence: [],
    }
  );
}

main();
