"use strict";
/**
 * Create Claude Code slash commands for the debugging memory system
 *
 * Commands are read from the plugin's commands/ directory (single source of truth)
 * and copied to the project's .claude/commands/ directory for npm users.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.createSlashCommands = createSlashCommands;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
/**
 * Get the package root directory (where commands/ lives)
 */
function getPackageRoot() {
    // When running from dist/src/setup/, go up to package root
    // Look for the commands/ directory as the indicator
    let dir = __dirname;
    while (dir !== path.dirname(dir)) {
        if (fs.existsSync(path.join(dir, 'commands')) &&
            fs.existsSync(path.join(dir, 'package.json'))) {
            return dir;
        }
        dir = path.dirname(dir);
    }
    // Fallback: assume we're 3 levels deep (dist/src/setup)
    return path.resolve(__dirname, '..', '..', '..');
}
/**
 * Read commands from the plugin's commands/ directory
 */
function loadPluginCommands() {
    const packageRoot = getPackageRoot();
    const pluginCommandsDir = path.join(packageRoot, 'commands');
    if (!fs.existsSync(pluginCommandsDir)) {
        console.warn('Plugin commands directory not found:', pluginCommandsDir);
        return [];
    }
    const commandFiles = fs.readdirSync(pluginCommandsDir)
        .filter(f => f.endsWith('.md'));
    return commandFiles.map(name => ({
        name,
        content: fs.readFileSync(path.join(pluginCommandsDir, name), 'utf-8')
    }));
}
async function createSlashCommands(projectRoot, force) {
    const commandsDir = path.join(projectRoot, '.claude', 'commands');
    if (!fs.existsSync(commandsDir)) {
        fs.mkdirSync(commandsDir, { recursive: true });
    }
    const commands = loadPluginCommands();
    if (commands.length === 0) {
        console.warn('No commands found to install');
        return 0;
    }
    let created = 0;
    for (const cmd of commands) {
        const cmdPath = path.join(commandsDir, cmd.name);
        if (force || !fs.existsSync(cmdPath)) {
            fs.writeFileSync(cmdPath, cmd.content);
            created++;
        }
    }
    return created;
}
//# sourceMappingURL=create-slash-commands.js.map