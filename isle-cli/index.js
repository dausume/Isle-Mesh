#!/usr/bin/env node

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

// Project root (parent of isle-cli)
const projectRoot = path.resolve(__dirname, '..');
const scriptsDir = path.join(__dirname, 'scripts');

// ─────────────────────────────────────────────────────────────────────────────
// COMMAND TABLE — the single source of truth for every CLI command.
//
// Add a command by adding ONE entry here; dispatch, validation, and aliases are
// all derived from it. No parallel switch-case to keep in sync (that duplication
// was the source of repeated wiring bugs). Every command dispatches uniformly:
//   bash <scripts>/<script> <subcommand> <extraArgs...>   (stdio inherited)
// with clean exit-code propagation (no Node stack traces on script failure).
//
//   script:     filename under scripts/
//   desc:       one-line description (help + docs)
//   aliases:    alternate names for the same command
//   docker:     true → warn if the user isn't in the docker group first
//   deprecated: string → print a deprecation notice before running
// ─────────────────────────────────────────────────────────────────────────────
const commands = {
  // Namespace modules (dispatch their own subcommands)
  app:      { script: 'app.sh',      desc: 'Mesh application management', docker: true },
  router:   { script: 'router.sh',   desc: 'Router and network management' },
  agent:    { script: 'agent.sh',    desc: 'Agent and bridge management' },
  mdns:     { script: 'mdns.sh',     desc: 'mDNS infrastructure (.local domains)' },
  dns:      { script: 'dns.sh',      desc: 'Router DNS management (.isle domains)' },
  trust:    { script: 'trust.sh',    desc: 'CA trust install-step (status/install/cert)' },
  certs:    { script: 'certs.sh',    desc: 'Per-domain leaf issuance from the isle CA' },
  security: { script: 'security.sh', desc: 'ISP visibility and network hardening' },

  // Lifecycle
  create:    { script: 'create.sh',    desc: 'Complete setup (agent + router + sample app)' },
  destroy:   { script: 'destroy.sh',   desc: 'Complete teardown (apps + agent + router)' },
  recover:   { script: 'boot-bringup.sh', desc: 'Idempotent full-isle bring-up (also run at boot)', aliases: ['boot'] },
  join:      { script: 'join.sh',      desc: 'Join an existing isle from a remote machine' },
  leave:     { script: 'leave.sh',     desc: 'Leave an isle (tear down remote agent)' },
  install:   { script: 'install.sh',   desc: 'Install dependencies (app/router/agent/all)' },
  uninstall: { script: 'uninstall.sh', desc: 'Uninstall components (app/router/all)' },

  // Discovery / onboarding
  discovery: { script: 'discovery.sh', desc: 'Turn node-discovery mode on/off' },
  scan:      { script: 'scan.sh',      desc: 'Discover hosts on the isle; flag ones without the agent' },
  devices:   { script: 'devices.sh',   desc: 'Known-devices ledger + onboarding decisions' },
  onboard:   { script: 'onboard.sh',   desc: 'Guided walkthrough to add a device to the mesh' },
  'remote-lease': { script: 'remote-lease.sh', desc: 'Pull an isle DHCP lease on the cable (remote node)' },
  hotplug:   { script: 'hotplug.sh',   desc: 'Internal: cable-plug handler (udev-invoked; role-aware)' },

  // Diagnostics / status
  status:    { script: 'status.sh',    desc: 'Comprehensive system status (all components)' },
  diagnose:  { script: 'diagnose.sh',  desc: 'Mesh-expansion hardware capacity diagnostic', aliases: ['capacity'] },
  test:      { script: 'test.sh',      desc: 'Run diagnostic tests (isle/mdns/all/check)' },

  // Tooling / maintenance
  usb:          { script: 'usb.sh',                desc: 'Make a USB drive into a portable isle-mesh installer' },
  ports:        { script: 'ports.sh',              desc: 'See/switch physical ethernet ports onto the isle' },
  permissions:  { script: 'permissions.sh',        desc: 'Manage file permissions' },
  'fix-docker': { script: 'fix-docker-cgroups.sh', desc: 'Check/fix Docker cgroup configuration issues' },
  dependencies: { script: 'check-dependencies.sh', desc: 'Manage system dependencies (check/install)', aliases: ['deps'] },

  // Deprecated (kept for backward compatibility)
  localhost: { script: 'mdns-app.sh', desc: 'DEPRECATED — use "isle mdns app"', deprecated: 'Use "isle mdns app" instead' },
};

// Resolve alias → canonical name.
const aliasMap = {};
for (const [name, def] of Object.entries(commands)) {
  for (const a of def.aliases || []) aliasMap[a] = name;
}
const resolve = (name) => aliasMap[name] || name;

// Old namespace-less commands → show a helpful "requires a namespace" error.
const namespacelessCommands = [
  'init', 'up', 'down', 'logs', 'ps', 'prune', 'scaffold', 'config',
  'discover', 'ssl', 'mesh-app-scaffolding', 'mesh-proxy', 'proxy',
  'embed-jinja', 'jinja', 'sample',
];

const makeExecutable = (filePath) => {
  try {
    execSync(`chmod +x ${filePath}`);
  } catch (err) {
    console.error(`Failed to make ${filePath} executable.`);
  }
};

// Ensure every command's script exists and is executable.
const validateScripts = () => {
  let ok = true;
  const seen = new Set();
  for (const def of Object.values(commands)) {
    const filePath = path.join(scriptsDir, def.script);
    if (seen.has(filePath)) continue;
    seen.add(filePath);
    try {
      const stats = fs.statSync(filePath);
      if ((stats.mode & fs.constants.S_IXUSR) === 0) {
        makeExecutable(filePath);
        if ((fs.statSync(filePath).mode & fs.constants.S_IXUSR) === 0) {
          console.error(`Error: Script ${filePath} is still not executable.`);
          ok = false;
        }
      }
    } catch (err) {
      console.error(`Error: Script ${filePath} does not exist.`);
      ok = false;
    }
  }
  return ok;
};

const checkDockerGroupMembership = () => {
  try {
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
      return;
    }
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
    }
  } catch (err) {
    console.warn('Warning: Error checking Docker group membership:', err.message);
  }
};

const showNamespaceError = (attemptedCommand) => {
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
  console.error('\x1b[31m%s\x1b[0m', '  ERROR: Command Requires Namespace');
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════');
  console.error('\x1b[33m%s\x1b[0m', `\nThe command '${attemptedCommand}' requires a namespace specifier.\n`);
  console.log('Isle CLI commands are organized into modules:\n');
  console.log('  \x1b[36m%s\x1b[0m', '• isle app <command>       - Mesh application management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle mdns <scope> <cmd>  - mDNS infrastructure (.local)');
  console.log('  \x1b[36m%s\x1b[0m', '• isle dns <command>       - Router DNS management (.isle)');
  console.log('  \x1b[36m%s\x1b[0m', '• isle router <command>    - Router and network management');
  console.log('  \x1b[36m%s\x1b[0m', '• isle agent <command>     - Agent and bridge management\n');
  console.log('Examples:');
  console.log('  \x1b[32m%s\x1b[0m', `  isle app ${attemptedCommand}`);
  console.log('  \x1b[32m%s\x1b[0m', `  isle router ${attemptedCommand}\n`);
  console.log('For more information, run: \x1b[36misle help\x1b[0m');
  console.error('\x1b[31m%s\x1b[0m', '═══════════════════════════════════════════════════════════════\n');
};

const showHelp = () => {
  console.log(`\x1b[1mIsle-Mesh CLI\x1b[0m - Zero-configuration mesh networking for containerized applications

╔═══════════════════════════════════════════════════════════════╗
║                    COMMAND STRUCTURE                          ║
╚═══════════════════════════════════════════════════════════════╝

Isle commands are organized into six modules (run \x1b[36misle <module> help\x1b[0m for each):

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
                          • Bridge creation and macvlan setup
                          • Nginx-to-router connectivity
                          • Bridge lifecycle management

  \x1b[36misle security <command>\x1b[0m  ISP visibility and network hardening
                          • Check ISP-visible exposure points
                          • Harden ports, mDNS, firewall
                          • Verify router air-gap isolation

╔═══════════════════════════════════════════════════════════════╗
║                    GLOBAL COMMANDS                            ║
╚═══════════════════════════════════════════════════════════════╝

  isle create             Complete setup (agent + router + sample app)
  isle destroy            Complete teardown (apps + agent + router)
  isle recover            Idempotent full-isle bring-up (also run at boot)
  isle join               Join an existing isle from a remote machine
  isle leave              Leave an isle (tear down remote agent)
  isle install [target]   Install dependencies (app/router/agent/all)
  isle uninstall [target] Uninstall components (app/router/all)

  isle discovery          Turn node-discovery mode on/off (gates detection)
  isle scan               Discover hosts on the isle; flag ones without the agent
  isle devices            Known-devices ledger + onboarding decisions
  isle onboard <ip|mac>   Guided walkthrough to add a device to the mesh
  isle remote-lease       Pull an isle DHCP lease on the cable (remote node)

  isle status             Show comprehensive system status (all components)
  isle diagnose           Mesh-expansion hardware capacity (USB/wifi headroom)
  isle test [suite]       Run diagnostic tests (isle/mdns/all/check)

  isle usb                Make a USB drive into a portable isle-mesh installer
  isle ports              See/switch physical ethernet ports onto the isle
  isle dependencies       Manage system dependencies (check/install)
  isle permissions        Manage file permissions
  isle fix-docker [cmd]   Check/fix Docker cgroup configuration issues
  isle help               Show this help message

╔═══════════════════════════════════════════════════════════════╗
║                    DETAILED HELP                             ║
╚═══════════════════════════════════════════════════════════════╝

For detailed command information:

  \x1b[32misle app help\x1b[0m           Show all mesh application commands
  \x1b[32misle mdns help\x1b[0m          Show all mDNS infrastructure commands (.local)
  \x1b[32misle dns help\x1b[0m           Show all DNS management commands (.isle)
  \x1b[32misle router help\x1b[0m        Show all router management commands
  \x1b[32misle agent help\x1b[0m         Show all agent commands

╔═══════════════════════════════════════════════════════════════╗
║                    QUICK START                               ║
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
`);
};

// ─────────────────────────────────────────────────────────────────────────────
// Dispatch
// ─────────────────────────────────────────────────────────────────────────────
const command = process.argv[2];
const subcommand = process.argv[3];
const extraArgs = process.argv.slice(4);

if (!validateScripts()) process.exit(1);

if (command === undefined || command === 'help') {
  showHelp();
  process.exit(0);
}

if (namespacelessCommands.includes(command)) {
  showNamespaceError(command);
  process.exit(1);
}

const def = commands[resolve(command)];
if (!def) {
  console.log('\x1b[31mUnknown command:\x1b[0m', command);
  console.log('\nUse \x1b[36misle help\x1b[0m to see available commands.');
  process.exit(1);
}

if (def.deprecated) {
  console.log('\x1b[33m%s\x1b[0m', `⚠️  WARNING: "isle ${command}" is deprecated`);
  console.log('\x1b[33m%s\x1b[0m', `   ${def.deprecated}`);
  console.log('');
}

if (def.docker) checkDockerGroupMembership();

const scriptPath = path.join(scriptsDir, def.script);
const args = [subcommand, ...extraArgs].filter(Boolean).join(' ');
try {
  execSync(`bash ${scriptPath} ${args}`, { stdio: 'inherit', cwd: projectRoot });
} catch (error) {
  // The script already printed its own error; exit with its code, no Node stack.
  process.exit(error.status || 1);
}
