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
    'mdns': path.join(__dirname, 'scripts', 'mdns.sh'),
    'dns': path.join(__dirname, 'scripts', 'dns.sh'),
    'security': path.join(__dirname, 'scripts', 'security.sh'),

    // Deprecated - backward compatibility
    'localhost': path.join(__dirname, 'scripts', 'mdns-app.sh'),

    // Top-level utilities
    'test': path.join(__dirname, 'scripts', 'test.sh'),
    'scan': path.join(__dirname, 'scripts', 'scan.sh'),
    'devices': path.join(__dirname, 'scripts', 'devices.sh'),
    'discovery': path.join(__dirname, 'scripts', 'discovery.sh'),
    'onboard': path.join(__dirname, 'scripts', 'onboard.sh'),
    'usb': path.join(__dirname, 'scripts', 'usb.sh'),
    'ports': path.join(__dirname, 'scripts', 'ports.sh'),
    'status': path.join(__dirname, 'scripts', 'status.sh'),
    'create': path.join(__dirname, 'scripts', 'create.sh'),
    'destroy': path.join(__dirname, 'scripts', 'destroy.sh'),
    'join': path.join(__dirname, 'scripts', 'join.sh'),
    'leave': path.join(__dirname, 'scripts', 'leave.sh'),
    'install': path.join(__dirname, 'scripts', 'install.sh'),
    'uninstall': path.join(__dirname, 'scripts', 'uninstall.sh'),
    'permissions': path.join(__dirname, 'scripts', 'permissions.sh'),
    'fix-docker': path.join(__dirname, 'scripts', 'fix-docker-cgroups.sh'),
    'dependencies': path.join(__dirname, 'scripts', 'check-dependencies.sh'),
    'deps': path.join(__dirname, 'scripts', 'check-dependencies.sh'),  // Alias
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
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.warn('\x1b[33m%s\x1b[0m', '  WARNING: Docker Permission Issue');
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.warn('\x1b[33m%s\x1b[0m', '\nYour user is not in the "docker" group.');
      console.warn('\x1b[33m%s\x1b[0m', 'You may need to use sudo for Docker commands.\n');
      console.warn('To fix this, run the following commands:\n');
      console.log('  \x1b[36m%s\x1b[0m', '1. sudo usermod -aG docker $USER');
      console.log('  \x1b[36m%s\x1b[0m', '2. newgrp docker');
      console.log('  \x1b[36m%s\x1b[0m', '   (or log out and log back in)\n');
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
      return true; // Changed to true - allow execution with warning
    }

    // Additional check: verify docker socket is accessible
    try {
      execSync('docker ps > /dev/null 2>&1');
    } catch (err) {
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.warn('\x1b[33m%s\x1b[0m', '  WARNING: Cannot access Docker daemon');
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
      console.warn('\x1b[33m%s\x1b[0m', '\nYou may be in the docker group, but the group change hasn\'t');
      console.warn('\x1b[33m%s\x1b[0m', 'taken effect yet in this session.\n');
      console.warn('To apply the group change, run:\n');
      console.log('  \x1b[36m%s\x1b[0m', 'newgrp docker');
      console.log('  \x1b[36m%s\x1b[0m', '(or log out and log back in)\n');
      console.warn('\x1b[33m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
      return true; // Changed to true - allow execution with warning
    }

    return true;
  } catch (err) {
    console.warn('Warning: Error checking Docker group membership:', err.message);
    return true; // Changed to true - allow execution even on check error
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
  console.log('Isle CLI commands are organized into five categories:\n');
  console.log('  \x1b[36m%s\x1b[0m', '• isle app <command>       - Mesh application management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle mdns <scope> <cmd>  - mDNS infrastructure (.local)');
  console.log('  \x1b[36m%s\x1b[0m', '• isle dns <command>       - Router DNS management (.isle)');
  console.log('  \x1b[36m%s\x1b[0m', '• isle router <command>    - Router and network management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle agent <command>     - Agent and bridge management\n');
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
    //process.exit(1);
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
    try {
      execSync(`bash ${scriptPaths['agent']} ${agentArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      // Agent script already displayed error message, just exit with same code
      process.exit(error.status || 1);
    }
    break;

  case 'mdns':
    // mDNS namespace (system, domain, app, sample, discover)
    const mdnsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['mdns']} ${mdnsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'dns':
    // DNS namespace (router DNS management - .isle domains)
    const dnsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['dns']} ${dnsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'security':
    // ISP visibility and network hardening
    const securityArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['security']} ${securityArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'localhost':
    // DEPRECATED - backward compatibility, redirect to mdns app
    console.log('\x1b[33m%s\x1b[0m', '⚠️  WARNING: "isle localhost" is deprecated');
    console.log('\x1b[33m%s\x1b[0m', '   Use "isle mdns app" instead');
    console.log('');
    const localhostArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['localhost']} ${localhostArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'create':
    // One-command setup: agent + router + sample app
    const createArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['create']} ${createArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'destroy':
    // Complete teardown: apps + agent + router
    const destroyArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['destroy']} ${destroyArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'join':
    // Join an existing isle from a remote machine
    const joinArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['join']} ${joinArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'leave':
    // Leave an isle (tear down remote agent)
    const leaveArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['leave']} ${leaveArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'install':
    // Install system dependencies with optional target (app/router/agent/all)
    const installArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['install']} ${installArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      // Install script already displayed error message, just exit with same code
      process.exit(error.status || 1);
    }
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

  case 'dependencies':
  case 'deps':
    // Manage Isle-Mesh dependencies
    const depsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['dependencies']} ${depsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'test':
    // Verify .isle routing goes through the router (not localhost/mDNS)
    const testArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['test']} ${testArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'scan':
    // Discover hosts on the isle and classify onboarded vs un-onboarded
    const scanArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['scan']} ${scanArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'devices':
    // Known-devices ledger (onboarded vs candidate, with decisions)
    const devicesArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['devices']} ${devicesArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'discovery':
    // Discovery mode (gate detection/onboarding on an explicit session)
    const discoveryArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['discovery']} ${discoveryArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'onboard':
    // Guided walkthrough to bring a discovered device onto the mesh
    const onboardArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['onboard']} ${onboardArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'usb':
    // Make a USB drive into a portable isle-mesh installer
    const usbArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['usb']} ${usbArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'ports':
    // See/switch physical ethernet ports onto the isle
    const portsArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    try {
      execSync(`bash ${scriptPaths['ports']} ${portsArgs}`, { stdio: 'inherit', cwd: projectRoot });
    } catch (error) {
      process.exit(error.status || 1);
    }
    break;

  case 'status':
    // Show unified system status
    const statusArgs = [subcommand, ...extraArgs].filter(Boolean).join(' ');
    execSync(`bash ${scriptPaths['status']} ${statusArgs}`, { stdio: 'inherit', cwd: projectRoot });
    break;

  case 'help':
  case undefined:
    console.log(`\x1b[1mIsle-Mesh CLI\x1b[0m - Zero-configuration mesh networking for containerized applications

╔═══════════════════════════════════════════════════════════════╗
║                    COMMAND STRUCTURE                          ║
╚═══════════════════════════════════════════════════════════════╝

Isle commands are organized into six main categories:

  \x1b[36misle app <command>\x1b[0m      Mesh application management
                          • Initialize and scaffold apps
                          • Start/stop services (init, up, down, logs, ps)
                          • Service discovery and SSL
                          • Configuration management

  \x1b[36misle mdns <scope> <command>\x1b[0m   mDNS infrastructure (physical machines)
                          • System installation (install/status/reload)
                          • Domain broadcasting (.local domains via Avahi)
                          • Localhost app management (up/down/logs)
                          • Sample environments and discovery

  \x1b[36misle dns <command>\x1b[0m      DNS management (router .isle domains)
                          • Router DNS discovery and status
                          • .isle domain mappings via dnsmasq
                          • Join protocol synchronization
                          • DNS verification and testing

  \x1b[36misle router <command>\x1b[0m   Router and network management
                          • OpenWRT router lifecycle
                          • Network configuration and testing
                          • Security and isolation verification

  \x1b[36misle agent <command>\x1b[0m    Agent and bridge management
                          • Automatic bridge creation (coming soon)
                          • Nginx-to-router connectivity
                          • Bridge lifecycle management

  \x1b[36misle security <command>\x1b[0m  ISP visibility and network hardening
                          • Check ISP-visible exposure points
                          • Harden ports, mDNS, firewall
                          • Verify router air-gap isolation

╔═══════════════════════════════════════════════════════════════╗
║                    GLOBAL COMMANDS                            ║
╚═══════════════════════════════════════════════════════════════╝

  isle status             Show comprehensive system status (all components)
  isle discovery          Turn node-discovery mode on/off (gates detection)
  isle scan               Discover hosts on the isle; flag ones without the agent
  isle devices            Known-devices ledger + onboarding decisions
  isle onboard <ip|mac>   Guided walkthrough to add a device to the mesh
  isle usb                Make a USB drive into a portable isle-mesh installer
  isle test [suite]       Run diagnostic tests (isle/mdns/all/check)
  isle create             Complete setup (agent + router + sample app)
  isle destroy            Complete teardown (apps + agent + router)
  isle join               Join an existing isle from a remote machine
  isle leave              Leave an isle (tear down remote agent)
  isle install [target]   Install dependencies (app/router/agent/all)
  isle uninstall [target] Uninstall components (app/router/all)
  isle dependencies       Manage system dependencies (check/install)
  isle permissions        Manage file permissions
  isle fix-docker [cmd]   Check/fix Docker cgroup configuration issues
  isle help               Show this help message

╔═══════════════════════════════════════════════════════════════╗
║                    DETAILED HELP                              ║
╚═══════════════════════════════════════════════════════════════╝

For detailed command information:

  \x1b[32misle app help\x1b[0m           Show all mesh application commands
  \x1b[32misle mdns help\x1b[0m          Show all mDNS infrastructure commands (.local)
  \x1b[32misle dns help\x1b[0m           Show all DNS management commands (.isle)
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
  case 'mesh-app-scaffolding':
  case 'mesh-proxy':
  case 'proxy':
  case 'embed-jinja':
  case 'jinja':
  case 'sample':
    showNamespaceError(command);
    process.exit(1);

  default:
    console.log('\x1b[31mUnknown command:\x1b[0m', command);
    console.log('\nUse \x1b[36mile help\x1b[0m to see available commands.');
    process.exit(1);
}