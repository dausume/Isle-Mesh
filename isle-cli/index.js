#!/usr/bin/env node

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

// Get the project root (parent of isle-cli)
const projectRoot = path.resolve(__dirname, '..');

const scriptPaths = {
    // Core commands
    'isle-core': path.join(__dirname, 'scripts', 'isle-core.sh'),
    'config': path.join(__dirname, 'scripts', 'config.sh'),
    'discover': path.join(__dirname, 'scripts', 'discover.sh'),

    // Project tools
    'mesh-proxy': path.join(__dirname, 'scripts', 'mesh-proxy.sh'),
    'embed-jinja': path.join(__dirname, 'scripts', 'embed-jinja.sh'),
    'ssl': path.join(__dirname, 'scripts', 'ssl.sh'),
    'scaffold': path.join(__dirname, 'scripts', 'scaffold.sh'),

    // System tools
    'mdns': path.join(__dirname, 'scripts', 'mdns.sh'),
    'sample': path.join(__dirname, 'scripts', 'sample.sh'),

    // Legacy/utility
    'test-cli': path.join(__dirname, 'scripts', 'test-cli.sh'),
    'uninstall': path.join(__dirname, 'scripts', 'uninstall.sh'),
    'run': path.join(__dirname, 'scripts', 'run.sh'),
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
const dockerCommands = ['run', 'mesh-proxy', 'proxy', 'embed-jinja', 'jinja', 'mdns', 'sample', 'localhost-mdns', 'scaffold'];

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
  // Core simplified commands
  case 'init':
  case 'up':
  case 'down':
  case 'prune':
  case 'logs':
  case 'ps':
    const coreArgs = [command, subcommand, ...extraArgs].filter(Boolean).join(' ');
    // Run core commands from the user's current working directory, not projectRoot
    execSync(`bash ${scriptPaths['isle-core']} ${coreArgs}`, { stdio: 'inherit' });
    break;
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
  case 'ssl':
    // SSL certificate management
    const sslArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['ssl']} ${sslArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'scaffold':
  case 'convert':
    // Scaffold a docker-compose app into a mesh-app
    const scaffoldArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['scaffold']} ${scaffoldArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'config':
    // Manage CLI configuration
    const configArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['config']} ${configArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'discover':
    // Discover .local domains
    const discoverArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['discover']} ${discoverArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;
  case 'help':
    console.log(`Isle-Mesh CLI - Orchestrate Isle-Mesh Docker Compose projects

╔═══════════════════════════════════════════════════════════════╗
║                    CORE COMMANDS (Simplified)                 ║
╚═══════════════════════════════════════════════════════════════╝

Getting Started:
  isle init [options]           - Initialize a new mesh-app project
    -f, --file FILE             - Convert from existing docker-compose.yml
    -o, --output DIR            - Output directory (default: current)
    -d, --domain DOMAIN         - Base domain (default: mesh-app.local)
    -n, --name NAME             - Project name (auto-detected)

Managing Services (like docker-compose):
  isle up [--build]             - Start mesh-app services
  isle down [-v]                - Stop mesh-app services
  isle logs [service]           - View service logs
  isle ps                       - List running services
  isle prune [-f]               - Clean up all mesh resources

Configuration:
  isle config set-project <path> - Set current mesh-app project
  isle config get-project        - Get current project path
  isle config show               - Show all configuration

Discovery:
  isle discover [command]        - Discover .local domains
    all                          - Discover from all sources (default)
    docker                       - Check Docker container labels
    nginx                        - Check Nginx configurations
    hosts                        - Check /etc/hosts entries
    mdns                         - Check mDNS/Avahi services
    test                         - Discover and test URL accessibility
    export [file]                - Export discovered domains to JSON

╔═══════════════════════════════════════════════════════════════╗
║                    ADVANCED COMMANDS                          ║
╚═══════════════════════════════════════════════════════════════╝

Project Tools:
  mesh-proxy|proxy [action]     - Manage mesh-proxy
    up                          - Start the mesh-proxy services
    down                        - Stop the mesh-proxy services
    build                       - Build the mesh-proxy builder
    logs                        - View mesh-proxy logs

  embed-jinja|jinja [action]    - Manage embed-jinja (framework automation)
    up/start                    - Start embed-jinja auto workflow
    down/stop                   - Stop embed-jinja services
    logs                        - View workflow logs
    app-logs                    - View application logs
    status                      - Show service status
    clean                       - Clean and reset

  ssl [action]                  - Manage SSL certificates
    generate                    - Generate basic SSL certificate
    generate-mesh               - Generate mesh SSL with subdomains
    list                        - List all certificates
    info <name>                 - Show certificate info
    verify <name>               - Verify certificate
    clean                       - Remove all certificates

  scaffold <compose-file> [opts] - Convert docker-compose to mesh-app
    -o, --output DIR            - Output directory
    -d, --domain DOMAIN         - Base domain
    -n, --name NAME             - Project name

System Tools:
  mdns [action]                 - Manage Isle Mesh mDNS system
    install/up                  - Install mDNS on host system
    uninstall/down              - Uninstall mDNS from host
    status                      - Check installation status

  sample <name> [action]        - Manage sample/demo environments
    localhost-mdns              - Hand-crafted localhost mDNS demo
    list                        - List available samples

Utility:
  test-cli                      - Test if the CLI is working
  uninstall                     - Uninstall this CLI tool globally
  help                          - Show this help message

╔═══════════════════════════════════════════════════════════════╗
║                    QUICK START EXAMPLES                       ║
╚═══════════════════════════════════════════════════════════════╝

1. Convert existing docker-compose app:
   isle init -f docker-compose.yml -d myapp.local
   isle up --build

2. Create new mesh-app from scratch:
   mkdir my-mesh-app && cd my-mesh-app
   isle init
   # Edit docker-compose.mesh-app.yml to add your services
   isle up

3. Manage running mesh-app:
   isle logs backend              # View backend logs
   isle ps                        # List services
   isle down                      # Stop all services
   isle prune                     # Clean up resources

4. Advanced usage:
   isle scaffold app.yml -o ./mesh-output
   isle ssl generate-mesh config/ssl.env.conf
   isle mdns install
    `);
    break;
  default:
    console.log('Unknown command. Use "isle help" to see available commands.');
    process.exit(1);
}