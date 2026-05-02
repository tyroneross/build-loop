/**
 * Create Claude Code slash commands for the debugging memory system
 *
 * Commands are read from the plugin's commands/ directory (single source of truth)
 * and copied to the project's .claude/commands/ directory for npm users.
 */

import * as fs from 'fs';
import * as path from 'path';

/**
 * Get the package root directory (where commands/ lives)
 */
function getPackageRoot(): string {
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
function loadPluginCommands(): Array<{ name: string; content: string }> {
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

export async function createSlashCommands(projectRoot: string, force?: boolean): Promise<number> {
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
