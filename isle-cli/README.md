# Isle-CLI

A CLI tool for managing Isle-Mesh: zero-configuration mesh networking for containerized applications.

## Installation

Use 'npm link' in the isle-cli folder to register the isle cli to your computer's npm:

```bash
cd isle-cli
npm link
```

After doing so, `isle` commands will be available globally wherever npm is available.

## Command Structure

Isle commands are organized into three main categories:

### `isle app <command>` - Mesh Application Management
Manage mesh applications, services, and configurations.

### `isle router <command>` - Router Management
Manage OpenWRT virtual routers for network isolation.

### `isle agent <command>` - Agent Management (Coming Soon)
Automatic bridge management between containers and routers.

## Quick Start

### 1. Initialize a Mesh Application

```bash
# Convert existing docker-compose
isle app init -f docker-compose.yml -d myapp.local

# Or create from scratch
mkdir my-mesh-app && cd my-mesh-app
isle app init -d myapp.local
```

### 2. Manage Application Services

```bash
# Start services
isle app up --build

# View logs
isle app logs [service-name]

# Stop services
isle app down

# List running services
isle app ps
```

### 3. Setup Router (Optional)

```bash
# Install router dependencies
sudo isle install router

# Initialize secure router
sudo isle router init

# Check router status
isle router status
```

## Common Commands

### Application Commands
- `isle app init` - Initialize new mesh-app
- `isle app up` - Start services
- `isle app down` - Stop services
- `isle app logs` - View logs
- `isle app discover` - Discover .local domains
- `isle app config` - Manage configuration
- `isle app scaffold` - Convert docker-compose

### Router Commands
- `isle router list` - List routers
- `isle router up <name>` - Start router
- `isle router down <name>` - Stop router
- `isle router status` - Show status
- `isle router help` - Detailed router help

### Global Commands
- `isle install [target]` - Install dependencies (app/router/agent/all)
- `isle help` - Show help message
- `isle uninstall` - Uninstall the CLI globally

## Getting Help

For detailed command information:

```bash
isle help              # Overview
isle app help          # All app commands
isle router help       # All router commands
isle agent help        # Agent commands (coming soon)
```

## Migration from Old Commands

If you used Isle CLI before the restructuring:

```bash
# Old command          →  New command
isle init              →  isle app init
isle up                →  isle app up
isle down              →  isle app down
isle logs              →  isle app logs
isle discover          →  isle app discover
isle config            →  isle app config
```

All old commands now require a namespace specifier (`app`, `router`, or `agent`).

## Documentation

### Isle image registry

`sudo isle registry setup` configures the existing registry service on the Isle
CA host. It selects the actual `br-mgmt` IPv4 address; `--address <IPv4>` selects
another address already assigned to that host. `--port` defaults to 5000. A
missing or ambiguous address refuses setup. Offline setup requires the exact
`registry:2` image recorded in the installation medium. New media builds include
that image; previously built media are not retroactively changed.

`isle registry verify` (or `--port <port>`) checks normal DNS and trusted HTTPS
without changing the host. Run it on each intended publisher/consumer. This
checks API access; a successful Docker push/pull remains a separate distribution
test. Setup is privileged and recreates the registry container while retaining
its data directory; it is not a read-only inspection command.

Offline Mesh App deployment also accepts an explicit immutable image reference
such as `registry.isle:5000/engines/board@sha256:<64-hex-digest>`. It reuses an
exact cached repository digest, or verifies the registry with the Isle root
before pulling that exact reference. Compose then runs with `--no-build --pull
never`. Mutable tags and external registries remain restricted to images
verified against the medium. This supports later Isle-published apps without
treating offline mode as a ban on the Isle's own local supply; it does not
automatically publish images or choose a compatible app version.

### Member service certificates

Normal app deployment uses authenticated automatic enrollment. On the core,
`sudo isle certs setup-authority` enables the bounded signing service behind
`https://certs.isle`. Pair a named member once with
`sudo isle certs authorize-member <device> --output <private-file>` and transfer
that file privately to the corresponding device. There, run
`sudo isle certs pair <private-file>` (or pass `--enrollment <private-file>` to
`isle join`). Delete the transfer copy after pairing. The credential is not a
CA key; it is scoped to one member and must never enter a package or repository.

Subsequent `isle app deploy` and Polari instance deployments generate a local
service key, send only a CSR over verified HTTPS, and install the verified chain.
The authority reserves each name to its authenticated member; another member
cannot claim it. Core service names are reserved. Renewal/retry keeps the member
key and ownership. `sudo isle certs revoke-member <device>` prevents further
issuance; already issued leaves remain valid until expiry. Pairing is an explicit
core authorization step, not an automatic grant to every device on the LAN.

Members keep their service private keys locally. For an operator-mediated
certificate exchange, use the existing Isle root trust and transfer the public
CSR and chain over a verified channel. On the member:

```bash
sudo isle certs request board-engines.isle --output /tmp/board-engines.csr
```

On the Isle CA host, the operator must verify the requesting device, CSR SHA256,
and ownership of the service name before signing. This command is explicit
authorization for that name; it does not perform automatic device enrollment or
reserve DNS ownership:

```bash
sudo isle certs sign /path/to/board-engines.csr --domain board-engines.isle --output /tmp/board-engines-chain.pem
```

Return only the public chain to the member:

```bash
sudo isle certs install board-engines.isle --certificate /path/to/board-engines-chain.pem
sudo isle certs reload
```

Import checks the Isle CA, exact service SAN, validity and matching local private
key before replacing the certificate. Renewal requests reuse the existing key.
Existing exchange output files are never overwritten. Neither private service
keys nor CA signing keys should be transferred. These commands do not register
the app or its DNS name; verify the actual app through trusted HTTPS from another
device after registration. For Polari instances, `isle polari instance verify
<name>` checks frontend and backend without redeploying.

`sudo isle certs check <domain>` checks an installed chain and matching key
without changing them. Deployment and `.isle` agent registration use `certs
ensure`: reuse a valid service identity, issue locally on the CA host if needed,
or stop with CSR instructions on a member. A member's failed certificate check
does not publish a new registry entry or start new app containers.

### Applying app placement

`isle polari app ensure <app>` validates the complete typed placement plan
before applying it. It executes only supported module-add and instance-deploy
actions, not the server's display command strings. Targets must be configured
on the executing device. Multiple missing modules needing a new instance are
grouped into one deployment. Failed steps stop the operation, and completion
requires a fresh placement result. This confirms module placement, not worker,
data-migration or physical-hardware readiness.

Mesh App engine declarations now default to the published HTTPS `.isle` domain,
so another device can resolve the provider. Existing explicit endpoint overrides
are retained. Previously written `engine.json` files are not migrated by a CLI
upgrade; review/redeploy those declarations as part of the app's placement work.

For comprehensive documentation, see:
- [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md) - Complete architecture
- [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Command reference
- [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md) - Adding new features
- [UNINSTALL-GUIDE.md](UNINSTALL-GUIDE.md) - Uninstallation guide
