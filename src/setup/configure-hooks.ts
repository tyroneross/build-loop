// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Configure hooks for build-loop native debugging memory.
 *
 * Build-loop ships its hooks via hooks/hooks.json. This helper intentionally
 * does not install standalone debugger hooks.
 */

export async function configureHooks(projectRoot: string): Promise<boolean> {
  void projectRoot;
  return false;
}
