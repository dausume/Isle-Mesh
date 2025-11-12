#!/usr/bin/env node

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

// Get the project root (parent of isle-cli)
const projectRoot = path.resolve(__dirname, '..');

const scriptPaths = {
    // Namespace commands
    'app': path.join(__dirname, 'scripts', 'app.sh'),
    'router': path.join(__dirname, 'scripts', 'router.sh'),
    'agent': path.join(__dirname, 'scripts', 'agent.sh'),

    // Top-level utilities
    'create': path.join(__dirname, 'scripts', 'create.sh'),
    'install': path.join(__dirname, 'scripts', 'install.sh'),
    'uninstall': path.join(__dirname, 'scripts', 'uninstall.sh'),
    'permissions': path.join(__dirname, 'scripts', 'permissions.sh'),
    'fix-docker': path.join(__dirname, 'scripts', 'fix-docker-cgroups.sh'),
};

const makeExecutable = (filePath) => {
    try {
      execSync(`chmod +x ${filePath}`);
      console.log(`Made ${filePath} executable.`);
    } catch (err) {
      console.error(`Failed to make ${filePath} executable.`);
    }
  };

  const validateScripts = () => {
    let allScriptsValid = true;

    Object.keys(scriptPaths).forEach((key) => {
      const filePath = scriptPaths[key];
      try {
        const stats = fs.statSync(filePath);
        if ((stats.mode & fs.constants.S_IXUSR) === 0) {
          console.log(`Script ${filePath} is not executable. Attempting to make it executable.`);
          makeExecutable(filePath);

          // Revalidate after attempting to make executable
          try {
            const newStats = fs.statSync(filePath);
            if ((newStats.mode & fs.constants.S_IXUSR) === 0) {
              console.error(`Error: Script ${filePath} is still not executable.`);
              allScriptsValid = false;
            }
          } catch (err) {
            console.error(`Error: Script ${filePath} does not exist.`);
            allScriptsValid = false;
          }
        }
      } catch (err) {
        console.error(`Error: Script ${filePath} does not exist.`);
        allScriptsValid = false;
      }
    });

    return allScriptsValid;
  };

const checkDockerGroupMembership = () => {
  try {
    // Check if user is in docker group
    const groups = execSync('groups', { encoding: 'utf8' });
    if (!groups.includes('docker')) {
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.error('\x1b[31m%s\x1b[0m', '  ERROR: Docker Permission Denied');
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.error('\x1b[33m%s\x1b[0m', '\nYour user is not in the "docker" group.');
      console.error('\x1b[33m%s\x1b[0m', 'This is required to run Docker commands without sudo.\n');
      console.error('To fix this, run the following commands:\n');
      console.log('  \x1b[36m%s\x1b[0m', '1. sudo usermod -aG docker $USER');
      console.log('  \x1b[36m%s\x1b[0m', '2. newgrp docker');
      console.log('  \x1b[36m%s\x1b[0m', '   (or log out and log back in)\n');
      console.error('\x1b[33m%s\x1b[0m', 'After adding yourself to the docker group, try again.');
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
      return false;
    }

    // Additional check: verify docker socket is accessible
    try {
      execSync('docker ps > /dev/null 2>&1');
    } catch (err) {
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.error('\x1b[31m%s\x1b[0m', '  WARNING: Cannot access Docker daemon');
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.error('\x1b[33m%s\x1b[0m', '\nYou may be in the docker group, but the group change hasn\'t');
      console.error('\x1b[33m%s\x1b[0m', 'taken effect yet in this session.\n');
      console.error('To apply the group change, run:\n');
      console.log('  \x1b[36m%s\x1b[0m', 'newgrp docker');
      console.log('  \x1b[36m%s\x1b[0m', '(or log out and log back in)\n');
      console.error('\x1b[33m%s\x1b[0m', 'Then try the command again.');
      console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
      return false;
    }

    return true;
  } catch (err) {
    console.error('Error checking Docker group membership:', err.message);
    return false;
  }
};

const command = process.argv[2];
const subcommand = process.argv[3];
const extraArgs = process.argv.slice(4);

// Helper function to show error for commands without namespace
const showNamespaceError = (attemptedCommand) => {
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
  console.error('\x1b[31m%s\x1b[0m', '  ERROR: Command Requires Namespace');
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
  console.error('\x1b[33m%s\x1b[0m', `\nThe command '${attemptedCommand}' requires a namespace specifier.\n`);
  console.log('Isle CLI commands are organized into three categories:\n');
  console.log('  \x1b[36m%s\x1b[0m', '• isle app <command>     - Mesh application management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle router <command>  - Router and network management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle agent <command>   - Agent and bridge management\n');
  console.log('Examples:');
  console.log('  \x1b[32m%s\x1b[0m', `  isle app ${attemptedCommand}`);
  console.log('  \x1b[32m%s\x1b[0m', `  isle router ${attemptedCommand}\n`);
  console.log('For more information, run: \x1b[36mile help\x1b[0m');
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
};

// Commands that require Docker (app commands will check internally)
const dockerCommands = ['app'];

// Validate scripts on initialization
if (!validateScripts()) {
  process.exit(1);
}

// Check Docker group membership for Docker-related commands
if (dockerCommands.includes(command)) {
  if (!checkDockerGroupMembership()) {
    process.exit(1);
  }
}

switch (command) {
  case 'app':
    // All mesh application commands
    const appArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['app']} ${appArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'router':
    // All router management commands
    const routerArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['router']} ${routerArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      // Router script already displayed error message, just exit with same code
      process.exit(error.status || 1);
    }
    break;

  case 'agent':
    // All agent and bridge management commands
    const agentArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['agent']} ${agentArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'create':
    // One-command setup: agent + router + sample app
    const createArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['create']} ${createArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'install':
    // Install system dependencies with optional target (app/router/agent/all)
    const installArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['install']} ${installArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'uninstall':
    // Uninstall with optional target (app/router/all)
    const uninstallArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['uninstall']} ${uninstallArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      // Exit with the same code as the script, but don't show Node.js error stack
      process.exit(error.status || 1);
    }
    break;

  case 'permissions':
    // Manage file permissions for Isle-Mesh
    const permissionsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['permissions']} ${permissionsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'fix-docker':
    // Fix Docker systemd D-Bus issues
    const fixDockerArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['fix-docker']} ${fixDockerArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'help':
  case undefined:
    console.log(`\x1b[1mIsle-Mesh CLI\x1b[0m - Zero-configuration mesh networking for containerized applications

╔═══════════════════════════════════════════════════════════════╗
║                    COMMAND STRUCTURE                          ║
╚═══════════════════════════════════════════════════════════════╝

Isle commands are organized into three main categories:

  \x1b[36misle app <command>\x1b[0m      Mesh application management
                          • Initialize and scaffold apps
                          • Start/stop services (init, up, down, logs, ps)
                          • Service discovery and SSL
                          • Configuration management

  \x1b[36misle router <command>\x1b[0m   Router and network management
                          • OpenWRT router lifecycle
                          • Network configuration and testing
                          • Security and isolation verification

  \x1b[36misle agent <command>\x1b[0m    Agent and bridge management
                          • Automatic bridge creation (coming soon)
                          • Nginx-to-router connectivity
                          • Bridge lifecycle management

╔═══════════════════════════════════════════════════════════════╗
║                    GLOBAL COMMANDS                            ║
╚═══════════════════════════════════════════════════════════════╝

  isle create             Complete setup (agent + router + sample app)
  isle install [target]   Install dependencies (app/router/agent/all)
  isle uninstall [target] Uninstall components (app/router/all)
  isle permissions        Manage file permissions
  isle fix-docker [cmd]   Check/fix Docker cgroup configuration issues
  isle help               Show this help message

╔═══════════════════════════════════════════════════════════════╗
║                    DETAILED HELP                              ║
╚═══════════════════════════════════════════════════════════════╝

For detailed command information:

  \x1b[32misle app help\x1b[0m           Show all mesh application commands
  \x1b[32misle router help\x1b[0m        Show all router management commands
  \x1b[32misle agent help\x1b[0m         Show all agent commands

╔═══════════════════════════════════════════════════════════════╗
║                    QUICK START                                ║
╚═══════════════════════════════════════════════════════════════╝

1. Complete setup with one command (recommended for first-time users):
   \x1b[33misle create\x1b[0m

   This sets up agent, router, and a sample app to demonstrate Isle Mesh.

2. Or set up components individually:

   a. Initialize a new mesh app:
      \x1b[33misle app init -d myapp.local\x1b[0m
      \x1b[33misle app up --build\x1b[0m

   b. Setup router for network isolation:
      \x1b[33msudo isle install router\x1b[0m
      \x1b[33msudo isle router init\x1b[0m

   c. Scaffold existing docker-compose:
      \x1b[33misle app scaffold docker-compose.yml -d myapp.local\x1b[0m
      \x1b[33misle app up\x1b[0m

For more examples and documentation, visit:
https://github.com/yourusername/IsleMesh
    `);
    break;

  // Handle old commands without namespace - show helpful error
  case 'init':
  case 'up':
  case 'down':
  case 'logs':
  case 'ps':
  case 'prune':
  case 'scaffold':
  case 'config':
  case 'discover':
  case 'ssl':
  case 'mesh-proxy':
  case 'proxy':
  case 'embed-jinja':
  case 'jinja':
  case 'mdns':
  case 'sample':
    showNamespaceError(command);
    process.exit(1);

  default:
    console.log('\x1b[31mUnknown command:\x1b[0m', command);
    console.log('\nUse \x1b[36mile help\x1b[0m to see available commands.');
    process.exit(1);
}