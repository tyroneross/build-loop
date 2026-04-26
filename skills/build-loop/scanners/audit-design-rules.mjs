#!/usr/bin/env node
/**
 * build-loop Phase 7, Gate C: Design Rule Scanner
 *
 * Zero-dependency, single-file static scanner for UI design-rule violations.
 * Mirrors audit-hardcoded-secrets.mjs architecture (Node >= 18, stdlib only).
 *
 * Pattern library covers:
 *   - SwiftUI / iOS: status pills, animation accessibility, raw UIColor RGB,
 *     literal font sizes, literal corner radii, icon-only accessibility labels
 *   - React / Web: Tailwind pill patterns, missing motion-reduce, raw hex colors,
 *     arbitrary text size values
 *
 * Exit codes:
 *   0 = clean
 *   1 = warnings only
 *   2 = must-fix findings present
 *
 * Usage:
 *   node audit-design-rules.mjs                          # scan cwd, auto-detect platform
 *   node audit-design-rules.mjs --root=/path/to/project
 *   node audit-design-rules.mjs --platform=swiftui
 *   node audit-design-rules.mjs --platform=react
 *   node audit-design-rules.mjs --targets=Views,Components
 *   node audit-design-rules.mjs --exts=.swift
 *   node audit-design-rules.mjs --json
 */

import fs from 'fs';
import path from 'path';

// =============================================================================
// ARG PARSING
// =============================================================================

function parseArgs(argv) {
  const out = {
    targets: null,
    exts: null,
    root: process.cwd(),
    platform: null,
    json: false,
  };
  for (const arg of argv.slice(2)) {
    if (arg.startsWith('--targets=')) {
      out.targets = arg.slice('--targets='.length).split(',').map((s) => s.trim()).filter(Boolean);
    } else if (arg.startsWith('--exts=')) {
      out.exts = arg.slice('--exts='.length).split(',').map((s) => s.trim()).filter(Boolean);
    } else if (arg.startsWith('--root=')) {
      out.root = path.resolve(arg.slice('--root='.length));
    } else if (arg.startsWith('--platform=')) {
      out.platform = arg.slice('--platform='.length).trim().toLowerCase();
    } else if (arg === '--json') {
      out.json = true;
    }
  }
  return out;
}

const args = parseArgs(process.argv);
const root = args.root;

// =============================================================================
// PATTERN LIBRARIES
// =============================================================================

/**
 * Rule shape:
 *   id               string   unique rule identifier
 *   severity         'must-fix' | 'warn' | 'info'
 *   description      string   human message
 *   pattern          RegExp   match in (comment-stripped) file content
 *   contextRequired  RegExp?  the match is skipped unless this regex also
 *                             matches within ±contextWindow chars of the match
 *   contextWindow    number?  characters each side to check (default 300)
 *   fileMustContain  RegExp?  whole-file guard (before comment stripping)
 *   invertFileCheck  boolean? if true, rule fires when file does NOT contain
 *                             fileMustContain (i.e. it's the absence check)
 *   pathExclude      RegExp?  skip this rule entirely if the file path matches
 */

const SWIFTUI_RULES = [
  {
    // Fires when .background(...) immediately followed (possibly with
    // intervening whitespace/newline) by .clipShape(Capsule()) and the
    // surrounding context contains a chip/badge/status/trend/score indicator.
    // The background argument itself must contain a color expression
    // (not just Theme.surfacePrimary which is fine for layouts).
    // We look for .opacity( or a status/score color variable as the tell
    // that this is a status pill, not just a card container.
    id: 'status-pill-background',
    severity: 'must-fix',
    description: 'Status indicators must use text color only, not background fills (CLAUDE.md "Signal" rule)',
    // Matches .background(<anything with opacity or color>).clipShape(Capsule())
    // allowing whitespace between the two modifiers.
    pattern: /\.background\([^)]*(?:\.opacity\([^)]+\)|[Cc]olor[^)]*)\)[\s\S]{0,60}?\.clipShape\(Capsule\(\)\)/,
    contextRequired: /(Chip|chip|Badge|badge|Status|status|Trend|trend|Score|score|Rating|rating|Label|label|Tag|tag|filterChip|dimChip|paceRating|trendColor)/,
    contextWindow: 400,
  },
  {
    // .repeatForever( in a file that does NOT have accessibilityReduceMotion
    id: 'animation-without-reducemotion',
    severity: 'must-fix',
    description: 'Continuous animations must respect accessibilityReduceMotion',
    pattern: /\.repeatForever\(/,
    fileMustContain: /accessibilityReduceMotion/,
    invertFileCheck: true,
  },
  {
    // Raw UIColor(red:green:blue:) literals outside Theme/
    id: 'uicolor-rgb-outside-theme',
    severity: 'must-fix',
    description: 'Use Theme tokens, not raw UIColor(red:green:blue:) literals',
    pattern: /UIColor\(red:\s*[\d.]+,\s*green:\s*[\d.]+,\s*blue:\s*[\d.]+/,
    pathExclude: /\/Theme\//,
  },
  {
    // Matches Text(...)\n?  .font(.system(size: N)) where the .font is NOT
    // chained on Image. We use a positive contextRequired to require "Text("
    // in the preceding 80 chars AND a negative contextRequired-style guard
    // (_preContextRequired) that we handle in the scanner loop.
    id: 'system-font-size-body-copy',
    severity: 'warn',
    description: 'Body copy should use Theme.fontBody/fontCaption tokens for Dynamic Type support',
    // Only capture the simple .font(.system(size: N)) form (no weight/design)
    // — weight-bearing calls like .system(size:weight:design:) are intentional
    pattern: /\.font\(\.system\(size:\s*\d+\)\)/,
    contextRequired: /Text\(/,
    contextWindow: 120,
    _preContextOnly: true,   // custom flag: contextRequired must match BEFORE the offset
    pathExclude: /\/Theme\/|Chip|chip|Badge|badge|Tabular|Numeric/,
  },
  {
    id: 'literal-corner-radius',
    severity: 'warn',
    description: 'Use Theme.cornerSmall/Medium/Large/XL tokens, not bare numeric corner radii',
    pattern: /(\.cornerRadius\(\s*\d+\s*\)|RoundedRectangle\(cornerRadius:\s*\d+\s*\))/,
    pathExclude: /\/Theme\//,
  },
  {
    // withAnimation(.easeInOut(duration:) or similar in file without reduceMotion guard
    id: 'animation-duration-without-reducemotion',
    severity: 'warn',
    description: 'Animations with duration should be gated on accessibilityReduceMotion',
    pattern: /(withAnimation\(\.\w+\(duration:|\.easeInOut\(duration:)/,
    fileMustContain: /accessibilityReduceMotion/,
    invertFileCheck: true,
  },
  {
    // Icon-only Image(systemName:) without an .accessibilityLabel within 300 chars
    id: 'sf-symbol-without-label',
    severity: 'warn',
    description: 'Icon-only Image(systemName:) should have explicit accessibilityLabel',
    // Negative lookahead is not reliable across multiline — use contextRequired inversion below
    pattern: /Image\(systemName:\s*"[^"]+"\)/,
    // We invert this: skip the finding IF accessibilityLabel appears nearby
    contextRequired: /^(?![\s\S]*accessibilityLabel)/,  // placeholder — handled in code
    contextWindow: 300,
    pathExclude: /Tests/,
    _accessibilityCheck: true,  // custom flag handled in scanner loop
  },
];

const WEB_RULES = [
  {
    id: 'status-pill-tailwind',
    severity: 'must-fix',
    description: 'Status indicators must use text color only, not background fills',
    pattern: /className="[^"]*\bbg-\w+-\d{3}\b[^"]*\brounded-(full|2xl|xl)\b[^"]*"/,
    contextRequired: /(chip|badge|status|trend|tag)/i,
    contextWindow: 300,
  },
  {
    id: 'animation-without-motion-reduce',
    severity: 'must-fix',
    description: 'Continuous animations must have motion-reduce: variant',
    pattern: /\b(animate-pulse|animate-spin|animate-bounce|animate-ping)\b(?![^"]*motion-reduce)/,
  },
  {
    id: 'hex-color-outside-theme',
    severity: 'warn',
    description: 'Use theme tokens, not raw hex color literals',
    pattern: /[:'"]\s*#[0-9a-fA-F]{3,6}\s*[;'"]?/,
    pathExclude: /tailwind\.config|theme|tokens|\.css$|\.scss$/,
  },
  {
    id: 'arbitrary-text-size',
    severity: 'warn',
    description: 'Use theme text size tokens, not arbitrary px values',
    pattern: /\btext-\[\d+px\]/,
  },
];

// =============================================================================
// PLATFORM CONFIG
// =============================================================================

const PLATFORM_CONFIG = {
  swiftui: {
    rules: SWIFTUI_RULES,
    defaultTargets: ['Views', 'Components', 'View.swift'],
    defaultExts: ['.swift'],
  },
  react: {
    rules: WEB_RULES,
    defaultTargets: ['src', 'components', 'app', 'pages'],
    defaultExts: ['.tsx', '.jsx', '.ts', '.js', '.html', '.vue'],
  },
  web: {
    rules: WEB_RULES,
    defaultTargets: ['src', 'components', 'app', 'pages'],
    defaultExts: ['.tsx', '.jsx', '.ts', '.js', '.html', '.vue'],
  },
};

// Dirs always skipped during walk
const SKIP_DIRS = new Set([
  'node_modules', '.build', 'DerivedData', '__tests__', '.git',
  '.turbo', '.vercel', 'out', '.cache', 'dist', 'build', 'coverage',
]);

const SKIP_PATH_FRAGMENTS = ['/Tests/', '/test/', '/__tests__/'];

// =============================================================================
// PLATFORM AUTO-DETECTION
// =============================================================================

function* walkForDetect(dir, depth = 0) {
  if (depth > 4) return;
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (SKIP_DIRS.has(e.name)) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) yield* walkForDetect(full, depth + 1);
    else yield full;
  }
}

function detectPlatform(scanRoot, targets) {
  const checkDirs = targets
    ? targets.map((t) => path.join(scanRoot, t))
    : [scanRoot];

  let hasSwift = false;
  let hasTsxJsx = false;

  outer: for (const dir of checkDirs) {
    if (!fs.existsSync(dir)) continue;
    const stat = fs.statSync(dir);
    const files = stat.isDirectory() ? [...walkForDetect(dir)] : [dir];
    for (const f of files) {
      const ext = path.extname(f);
      if (ext === '.swift') { hasSwift = true; break outer; }
      if (ext === '.tsx' || ext === '.jsx') hasTsxJsx = true;
    }
  }
  if (hasSwift) return 'swiftui';
  if (hasTsxJsx) return 'react';
  return 'web';
}

// =============================================================================
// FILE WALKER
// =============================================================================

function* walk(dir) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const entry of entries) {
    if (entry.name.startsWith('.') && SKIP_DIRS.has(entry.name.slice(1))) continue;
    if (SKIP_DIRS.has(entry.name)) continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(fullPath);
    else yield fullPath;
  }
}

// =============================================================================
// COMMENT STRIPPING (Swift and JS/TS)
// For design rules we strip comments — commented-out code is not rendered UI.
// =============================================================================

function stripComments(source, ext) {
  if (ext === '.swift' || ext === '.ts' || ext === '.tsx' || ext === '.js' || ext === '.jsx' || ext === '.mjs') {
    return source
      .replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length))  // block comments → spaces (preserve offsets)
      .replace(/(^|\s)\/\/.*$/gm, (m, p1) => p1 + ' '.repeat(m.length - p1.length));  // line comments → spaces
  }
  if (ext === '.html' || ext === '.vue') {
    return source.replace(/<!--[\s\S]*?-->/g, (m) => ' '.repeat(m.length));
  }
  return source;
}

// =============================================================================
// OFFSET → LINE NUMBER
// =============================================================================

function lineNumberAt(source, offset) {
  let line = 1;
  for (let i = 0; i < offset && i < source.length; i++) {
    if (source[i] === '\n') line++;
  }
  return line;
}

// =============================================================================
// SNIPPET: single line of context around match
// =============================================================================

function snippetAt(source, offset) {
  const start = source.lastIndexOf('\n', offset) + 1;
  const end = source.indexOf('\n', offset);
  return source.slice(start, end === -1 ? undefined : end).trim();
}

// =============================================================================
// MAIN SCAN
// =============================================================================

const platform = args.platform ?? detectPlatform(root, args.targets);
const config = PLATFORM_CONFIG[platform] ?? PLATFORM_CONFIG.swiftui;
const rules = config.rules;
const exts = new Set(args.exts ?? config.defaultExts);
const scanTargets = args.targets ?? config.defaultTargets;

// Collect files
const filesToScan = [];

for (const target of scanTargets) {
  // Target can be a path fragment (substring match) or a resolvable path
  const absoluteTarget = path.isAbsolute(target) ? target : path.join(root, target);

  if (fs.existsSync(absoluteTarget)) {
    const stat = fs.statSync(absoluteTarget);
    const candidates = stat.isDirectory() ? [...walk(absoluteTarget)] : [absoluteTarget];
    for (const f of candidates) {
      if (exts.has(path.extname(f)) && !filesToScan.includes(f)) {
        // Apply global path exclusions
        const skip = SKIP_PATH_FRAGMENTS.some((frag) => f.includes(frag));
        if (!skip) filesToScan.push(f);
      }
    }
  } else {
    // Treat as substring: walk root and include files whose paths contain this target
    for (const f of walk(root)) {
      if (!exts.has(path.extname(f))) continue;
      if (!f.includes(target)) continue;
      if (filesToScan.includes(f)) continue;
      const skip = SKIP_PATH_FRAGMENTS.some((frag) => f.includes(frag));
      if (!skip) filesToScan.push(f);
    }
  }
}

const findings = [];

for (const filePath of filesToScan) {
  const ext = path.extname(filePath);
  let source;
  try { source = fs.readFileSync(filePath, 'utf8'); } catch { continue; }

  const stripped = stripComments(source, ext);

  for (const rule of rules) {
    // pathExclude check
    if (rule.pathExclude && rule.pathExclude.test(filePath)) continue;

    // fileMustContain / invertFileCheck — whole-file guard (use original source)
    if (rule.fileMustContain) {
      const fileHas = rule.fileMustContain.test(source);
      if (rule.invertFileCheck) {
        // Fire only when file does NOT contain the guard — skip file if it does
        if (fileHas) continue;
      } else {
        if (!fileHas) continue;
      }
    }

    // Reset regex state
    rule.pattern.lastIndex = 0;

    let match;
    const re = new RegExp(rule.pattern.source, rule.pattern.flags.includes('g') ? rule.pattern.flags : rule.pattern.flags + 'g');

    while ((match = re.exec(stripped)) !== null) {
      const matchOffset = match.index;
      const cw = rule.contextWindow ?? 300;
      const contextStart = Math.max(0, matchOffset - cw);
      const contextEnd = Math.min(stripped.length, matchOffset + match[0].length + cw);
      const context = stripped.slice(contextStart, contextEnd);

      // Special accessibility check: skip if accessibilityLabel appears nearby
      if (rule._accessibilityCheck) {
        if (/accessibilityLabel/.test(context)) continue;
      }

      // contextRequired check (skip if nearby context doesn't match)
      if (rule.contextRequired && !rule._accessibilityCheck) {
        if (rule._preContextOnly) {
          // Only check the content BEFORE the match (preceding contextWindow chars)
          const preContext = stripped.slice(contextStart, matchOffset);
          if (!rule.contextRequired.test(preContext)) continue;
        } else {
          if (!rule.contextRequired.test(context)) continue;
        }
      }

      const lineNum = lineNumberAt(source, matchOffset);
      const snippet = snippetAt(source, matchOffset);
      const relPath = path.relative(root, filePath);

      findings.push({
        rule: rule.id,
        severity: rule.severity,
        file: relPath,
        line: lineNum,
        snippet: snippet.length > 120 ? snippet.slice(0, 120) + '...' : snippet,
        description: rule.description,
      });

      // For rules without 'g' flag in original, only match once per file
      if (!rule.pattern.flags.includes('g')) break;
    }
  }
}

// =============================================================================
// OUTPUT
// =============================================================================

const summary = {
  mustFix: findings.filter((f) => f.severity === 'must-fix').length,
  warn: findings.filter((f) => f.severity === 'warn').length,
  info: findings.filter((f) => f.severity === 'info').length,
};

const exitCode = summary.mustFix > 0 ? 2 : summary.warn > 0 ? 1 : 0;

if (args.json) {
  const output = {
    platform,
    rulesEvaluated: rules.length,
    filesScanned: filesToScan.length,
    findings,
    summary,
    exit: exitCode,
  };
  console.log(JSON.stringify(output, null, 2));
  process.exit(exitCode);
}

// Text mode
console.log(`audit-design-rules: ${platform} pack, ${rules.length} rules`);
console.log(`files scanned: ${filesToScan.length}`);
console.log('');

function printGroup(severity, label) {
  const group = findings.filter((f) => f.severity === severity);
  console.log(`${label}: ${group.length}`);
  for (const f of group) {
    console.log(`  ${f.file}:${f.line} [${f.rule}] ${f.description}`);
    console.log(`    → ${f.snippet}`);
  }
  if (group.length) console.log('');
}

printGroup('must-fix', 'must-fix');
printGroup('warn', 'warn');
printGroup('info', 'info');

console.log(`Exit: ${exitCode}`);
process.exit(exitCode);
