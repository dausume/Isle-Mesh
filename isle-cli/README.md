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

For comprehensive documentation, see:
- [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md) - Complete architecture
- [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Command reference
- [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md) - Adding new features
- [UNINSTALL-GUIDE.md](UNINSTALL-GUIDE.md) - Uninstallation guide