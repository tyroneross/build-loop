"use strict";
// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
/**
 * Setup module exports
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.uninstall = exports.injectClaudeMd = exports.configureHooks = exports.createSlashCommands = void 0;
var create_slash_commands_1 = require("./create-slash-commands");
Object.defineProperty(exports, "createSlashCommands", { enumerable: true, get: function () { return create_slash_commands_1.createSlashCommands; } });
var configure_hooks_1 = require("./configure-hooks");
Object.defineProperty(exports, "configureHooks", { enumerable: true, get: function () { return configure_hooks_1.configureHooks; } });
var inject_claude_md_1 = require("./inject-claude-md");
Object.defineProperty(exports, "injectClaudeMd", { enumerable: true, get: function () { return inject_claude_md_1.injectClaudeMd; } });
var uninstall_1 = require("./uninstall");
Object.defineProperty(exports, "uninstall", { enumerable: true, get: function () { return uninstall_1.uninstall; } });
//# sourceMappingURL=index.js.map