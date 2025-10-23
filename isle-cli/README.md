# Isle-CLI

A CLI tool for managing and orchestrating Isle-Mesh Docker Compose projects.

## Installation

Use 'npm link' in the isle-cli folder to register the isle cli to your computer's npm:

```bash
cd isle-cli
npm link
```

After doing so, `isle` commands will be available globally wherever npm is available.

## Usage

```bash
isle <command> [subcommand] [options]
```

### Available Commands

**Project Management:**
- `isle mesh-proxy [action]` or `isle proxy [action]` - Manage mesh-proxy
- `isle embed-jinja [action]` or `isle jinja [action]` - Manage embed-jinja
- `isle localhost-mdns [action]` or `isle mdns [action]` - Manage localhost-mdns

**Utility Commands:**
- `isle test-cli` - Test if the CLI is working
- `isle help` - Show help message
- `isle uninstall` - Uninstall the CLI globally

### Examples

```bash
# Start mesh-proxy builder
isle mesh-proxy build

# Start localhost-mdns stack
isle mdns up

# View logs for embed-jinja
isle jinja logs

# Stop localhost-mdns
isle localhost-mdns down

# Run mesh-proxy watcher
isle proxy watch

# View status of localhost-mdns services
isle mdns status
```

### Project-Specific Actions

Each project supports common actions like:
- `up` - Start services in detached mode
- `down` - Stop services
- `build` - Build images
- `rebuild` - Stop, rebuild, and restart
- `logs` - View logs
- `status` - Show running containers

Use `isle <project> help` to see project-specific options.