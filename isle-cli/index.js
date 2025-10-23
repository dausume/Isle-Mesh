#!/usr/bin/env node

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

// Get the project root (parent of isle-cli)
const projectRoot = path.resolve(__dirname, '..');

const scriptPaths = {
    'test-cli': path.join(__dirname, 'scripts', 'test-cli.sh'),
    'uninstall': path.join(__dirname, 'scripts', 'uninstall.sh'),
    'run': path.join(__dirname, 'scripts', 'run.sh'),
    'mesh-proxy': path.join(__dirname, 'scripts', 'mesh-proxy.sh'),
    'embed-jinja': path.join(__dirname, 'scripts', 'embed-jinja.sh'),
    'mdns': path.join(__dirname, 'scripts', 'mdns.sh'),
    'sample': path.join(__dirname, 'scripts', 'sample.sh'),
    'localhost-mdns': path.join(__dirname, 'scripts', 'localhost-mdns.sh'),
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

// Commands that require Docker
const dockerCommands = ['run', 'mesh-proxy', 'proxy', 'embed-jinja', 'jinja', 'mdns', 'sample', 'localhost-mdns'];

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
  case 'test-cli':
    execSync(`bash ${scriptPaths['test-cli']}`, { stdio: 'inherit' });
    break;
  case 'uninstall':
    execSync(`bash ${scriptPaths['uninstall']}`, { stdio: 'inherit' });
    break;
  case 'run':
    if (subcommand) {
      // Run a specific project with subcommand
      const projectScript = scriptPaths[subcommand];
      if (projectScript) {
        const args = extraArgs.join(' ');
        execSync(`bash ${projectScript} ${args}`, { stdio: 'inherit', cwd: projectRoot });
      } else {
        console.log(`Unknown project: ${subcommand}`);
        console.log('Available projects: mesh-proxy, embed-jinja, localhost-mdns');
        process.exit(1);
      }
    } else {
      // Legacy run command
      execSync(`bash ${scriptPaths['run']}`, { stdio: 'inherit' });
    }
    break;
  case 'mesh-proxy':
  case 'proxy':
    const proxyArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['mesh-proxy']} ${proxyArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'embed-jinja':
  case 'jinja':
    const jinjaArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['embed-jinja']} ${jinjaArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'mdns':
    // Real mDNS system setup
    const mdnsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['mdns']} ${mdnsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'sample':
    // Sample/demo environments
    const sampleArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['sample']} ${sampleArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'localhost-mdns':
    // Legacy support - redirect to sample command
    console.log("Note: 'isle localhost-mdns' is now 'isle sample localhost-mdns'");
    const legacyArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['localhost-mdns']} ${legacyArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'help':
    console.log(`Isle-Mesh CLI - Orchestrate Isle-Mesh Docker Compose projects

Usage:
  isle <command> [subcommand] [options]

Commands:
  test-cli              - Test if the CLI is working
  uninstall             - Uninstall this CLI tool globally

  run [project]         - Run a specific project using Docker Compose

  mesh-proxy|proxy [action] - Manage mesh-proxy
    up                  - Start the mesh-proxy services
    down                - Stop the mesh-proxy services
    build               - Build the mesh-proxy builder
    logs                - View mesh-proxy logs

  embed-jinja|jinja [action] - Manage embed-jinja (framework automation)
    up/start            - Start embed-jinja auto workflow
    down/stop           - Stop embed-jinja services
    logs                - View workflow logs
    app-logs            - View application logs
    status              - Show service status
    clean               - Clean and reset

  mdns [action]         - Manage Isle Mesh mDNS system setup (REAL infrastructure)
    install/up          - Install mDNS on host system
    uninstall/down      - Uninstall mDNS from host
    status              - Check installation status
    broadcast           - Test mDNS broadcast

  sample <name> [action] - Manage sample/demo environments (EXAMPLES)
    localhost-mdns      - Hand-crafted localhost mDNS demo
    list                - List available samples

  help                  - Show this help message

Examples:
  isle mesh-proxy build                # Build mesh proxy
  isle jinja up                        # Start embed-jinja automation
  isle mdns install                    # Install real mDNS system
  isle sample localhost-mdns up        # Start demo environment
  isle sample list                     # List sample projects
    `);
    break;
  default:
    console.log('Unknown command. Use "isle help" to see available commands.');
    process.exit(1);
}