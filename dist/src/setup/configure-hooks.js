"use strict";
/**
 * Configure Claude Code hooks for automatic debugging memory integration
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
exports.configureHooks = configureHooks;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
async function configureHooks(projectRoot) {
    const settingsPath = path.join(projectRoot, '.claude', 'settings.json');
    const claudeDir = path.dirname(settingsPath);
    if (!fs.existsSync(claudeDir)) {
        fs.mkdirSync(claudeDir, { recursive: true });
    }
    let settings = {};
    if (fs.existsSync(settingsPath)) {
        try {
            settings = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
        }
        catch {
            settings = {};
        }
    }
    // Initialize hooks object if needed
    if (!settings.hooks || typeof settings.hooks !== 'object') {
        settings.hooks = {};
    }
    const hooks = settings.hooks;
    const stopHooks = (hooks.Stop || []);
    // Check for existing debugger hook in both old and new formats
    const hasMemoryHook = stopHooks.some((h) => {
        // Check new format
        if (h.hooks) {
            return h.hooks.some(hook => hook.command?.includes('claude-code-debugger'));
        }
        // Check legacy format
        return h.command?.includes('claude-code-debugger');
    });
    if (!hasMemoryHook) {
        // Use new matcher-based format
        stopHooks.push({
            matcher: "",
            hooks: [
                {
                    type: 'command',
                    command: 'npx @tyroneross/claude-code-debugger mine --days 1 --store 2>/dev/null || true'
                }
            ]
        });
        hooks.Stop = stopHooks;
        settings.hooks = hooks;
        fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
        return true;
    }
    return false;
}
//# sourceMappingURL=configure-hooks.js.map