"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Debugging Memory System - Type Definitions
 *
 * Defines the structure of incidents, patterns, and memory operations.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_TOKEN_BUDGET = void 0;
/**
 * Default token budget configuration
 */
exports.DEFAULT_TOKEN_BUDGET = {
    total: 2500,
    allocated: {
        patterns: 750,
        incidents: 1500,
        metadata: 250,
    },
    perItem: {
        pattern: 120,
        incident: 200,
        summary: 100,
    },
};
//# sourceMappingURL=types.js.map