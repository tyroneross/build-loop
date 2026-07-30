// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Jaro-Winkler similarity, returning a score from 0 to 1.
 *
 * Build Loop only needs this one metric from `natural`. Keeping it local avoids
 * importing that package's storage adapters during MCP startup.
 */
export function jaroWinklerDistance(a: string, b: string): number {
  if (a === b) return 1;
  if (!a || !b) return 0;

  const s1 = a.length <= b.length ? a : b;
  const s2 = a.length <= b.length ? b : a;
  const matchDistance = Math.max(Math.floor(s2.length / 2) - 1, 0);
  const s1Matches = new Array<boolean>(s1.length).fill(false);
  const s2Matches = new Array<boolean>(s2.length).fill(false);

  let matches = 0;
  for (let i = 0; i < s1.length; i += 1) {
    const start = Math.max(0, i - matchDistance);
    const end = Math.min(i + matchDistance + 1, s2.length);

    for (let j = start; j < end; j += 1) {
      if (s2Matches[j] || s1[i] !== s2[j]) continue;
      s1Matches[i] = true;
      s2Matches[j] = true;
      matches += 1;
      break;
    }
  }

  if (matches === 0) return 0;

  let transpositions = 0;
  let s2Index = 0;
  for (let i = 0; i < s1.length; i += 1) {
    if (!s1Matches[i]) continue;
    while (!s2Matches[s2Index]) s2Index += 1;
    if (s1[i] !== s2[s2Index]) transpositions += 1;
    s2Index += 1;
  }

  const m = matches;
  const jaro =
    (m / s1.length + m / s2.length + (m - transpositions / 2) / m) / 3;

  let prefix = 0;
  const maxPrefix = Math.min(4, a.length, b.length);
  while (prefix < maxPrefix && a[prefix] === b[prefix]) {
    prefix += 1;
  }

  return jaro + prefix * 0.1 * (1 - jaro);
}
