# IsleMesh CLI Documentation Index

This directory contains complete documentation of the IsleMesh CLI architecture and all available commands.

## Documentation Files

### Quick Start
- **[QUICK-REFERENCE.md](QUICK-REFERENCE.md)** - Command reference and common tasks
  - All CLI commands in table format
  - Common workflows
  - Troubleshooting tips
  - Quick examples
  - **Best for**: Quick lookup of commands

### Architecture & Design
- **[CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)** - Complete CLI architecture documentation
  - Command structure and routing
  - Current commands and organization
  - Networking and router features
  - Execution patterns
  - Configuration management
  - Validation and error handling
  - **Best for**: Understanding how CLI works

### Extension Guide
- **[EXTENSION-GUIDE.md](EXTENSION-GUIDE.md)** - How to add new features
  - File locations and line numbers
  - Detailed code examples
  - Implementation patterns
  - Testing guidance
  - Bash script patterns
  - Error handling examples
  - **Best for**: Adding new features to CLI

### Feature Documentation
- **[DISCOVER.md](DISCOVER.md)** - Network discovery command details
  - Discovery methods explanation
  - All subcommands
  - Detection examples
  - Advanced features
  - **Best for**: Understanding service discovery

### Summary
- **[CLI-EXPLORATION-SUMMARY.txt](CLI-EXPLORATION-SUMMARY.txt)** - High-level summary
  - Current commands overview
  - Router management features
  - Network discovery capabilities
  - Architecture patterns
  - Extension recommendations
  - **Best for**: Getting oriented quickly

## File Organization

```
isle-cli/
├── index.js                       # Main entry point (command routing)
├── package.json                   # Node package config
├── README.md                      # Original README
├── DISCOVER.md                    # Discovery command details
├── 
├── CLI-ARCHITECTURE.md            # Complete architecture documentation
├── EXTENSION-GUIDE.md             # How to extend with new commands
├── QUICK-REFERENCE.md             # Command reference and common tasks
├── CLI-EXPLORATION-SUMMARY.txt    # High-level summary
├── INDEX.md                       # This file
│
└── scripts/
    ├── router.sh                  # Router management commands
    ├── discover.sh                # Network/service discovery
    ├── isle-core.sh               # Core project commands
    ├── config.sh                  # Configuration management
    ├── install.sh                 # System dependencies
    ├── mesh-proxy.sh              # Mesh-proxy tool
    ├── embed-jinja.sh             # Embed-jinja tool
    ├── ssl.sh                     # SSL certificate management
    ├── scaffold.sh                # Docker-compose conversion
    ├── mdns.sh                    # mDNS system management
    ├── sample.sh                  # Sample environments
    ├── uninstall.sh               # CLI uninstallation
    ├── run.sh                     # Legacy run command
    └── localhost-mdns.sh          # Legacy mDNS
```

## Quick Navigation Guide

### I want to...

#### Use the CLI
1. Start with [QUICK-REFERENCE.md](QUICK-REFERENCE.md)
2. Look up commands in command reference tables
3. Follow example workflows
4. Use troubleshooting section if needed

#### Understand how CLI works
1. Read [CLI-EXPLORATION-SUMMARY.txt](CLI-EXPLORATION-SUMMARY.txt)
2. Review [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)
3. Check script execution patterns
4. Understand command routing

#### Add a new command
1. Review [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md)
2. Look at implementation examples (3 detailed examples provided)
3. Check file locations and line numbers
4. Follow bash script patterns
5. Update index.js and help text

#### Discover services
1. Check [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Discovery section
2. Read [DISCOVER.md](DISCOVER.md) - Full discovery documentation
3. Try commands: `isle discover`, `isle discover test`
4. Review detection methods

#### Manage router
1. Check [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Router section
2. Review [AUTOMATED-ROUTER-SETUP.md](../AUTOMATED-ROUTER-SETUP.md) (in project root)
3. Follow router workflow: setup -> verify -> use
4. Check openwrt-router/README.md for detailed info

#### Troubleshoot issues
1. See [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Error Messages section
2. Check [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md) - Validation section
3. Review [CLI-EXPLORATION-SUMMARY.txt](CLI-EXPLORATION-SUMMARY.txt)

## Command Categories

### Core Project Commands
- `init` - Initialize mesh-app
- `up`, `down` - Start/stop services
- `logs`, `ps` - Monitor services
- `prune` - Clean up resources

**Documentation**: [QUICK-REFERENCE.md](QUICK-REFERENCE.md)

### Router Management
- `router test basic` - Automated setup
- `router test verify` - Verify setup
- `router status` - Show status
- `router configure` - Configure router
- `router detect` - Detect hardware

**Documentation**: [QUICK-REFERENCE.md](QUICK-REFERENCE.md), [AUTOMATED-ROUTER-SETUP.md](../AUTOMATED-ROUTER-SETUP.md)

### Network Discovery
- `discover` - Discover all services
- `discover docker` - Check Docker
- `discover mdns` - Check mDNS
- `discover test` - Test URLs
- `discover export` - Export to JSON

**Documentation**: [QUICK-REFERENCE.md](QUICK-REFERENCE.md), [DISCOVER.md](DISCOVER.md)

### Project Tools
- `mesh-proxy` - Manage proxy
- `embed-jinja` - Manage automation
- `ssl` - Manage certificates
- `scaffold` - Convert docker-compose
- `config` - Manage configuration

**Documentation**: [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)

### System Tools
- `install` - Install dependencies
- `mdns` - Manage mDNS
- `sample` - Manage samples

**Documentation**: [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)

## Key Concepts

### Command Routing
```
Node.js (index.js)
    ↓ parses arguments
Bash Script (scripts/*.sh)
    ↓ implements command
Output
```

### Router Network Interfaces
- Management: 192.168.100.1 (eth0)
- Isle Network: 10.100.0.1 (eth1)

### Discovery Methods
1. Docker container labels
2. Nginx configurations
3. /etc/hosts entries
4. mDNS/Avahi services

### Bash Script Patterns
- Color-coded logging ([INFO], [✓], [✗], [⚠])
- Function-based architecture
- Main case statement for subcommands
- Helper validation functions

## Common Tasks

### Setup
```bash
sudo isle install router          # Install dependencies
sudo isle router test basic       # Setup test environment
isle discover                     # Find all services
```

### Development
```bash
isle init                         # Initialize project
isle up --build                   # Start services
isle logs backend                 # View logs
isle discover test                # Test URLs
```

### Troubleshooting
```bash
isle router status                # Check router
isle discover                     # Discover services
sudo isle router test reconfigure # Fix network
```

## File Sizes & Content

| File | Size | Focus |
|------|------|-------|
| CLI-ARCHITECTURE.md | 13KB | Architecture & design |
| EXTENSION-GUIDE.md | 15KB | How to extend |
| QUICK-REFERENCE.md | 10KB | Command reference |
| DISCOVER.md | 12KB | Discovery details |
| CLI-EXPLORATION-SUMMARY.txt | 14KB | Overview summary |
| INDEX.md | This file | Navigation |

## Creating New Commands

See [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md) for:
- Implementation examples (3 detailed examples)
- File locations with line numbers
- Bash script patterns
- Testing guidance
- Error handling patterns

## Important File Locations

### CLI Entry Point
- `/home/dustin/Desktop/IsleMesh/isle-cli/index.js` (500+ lines)

### Command Scripts
- `/home/dustin/Desktop/IsleMesh/isle-cli/scripts/router.sh` (500 lines)
- `/home/dustin/Desktop/IsleMesh/isle-cli/scripts/discover.sh` (480 lines)

### Router Infrastructure
- `/home/dustin/Desktop/IsleMesh/openwrt-router/scripts/` - Router scripts
- `/home/dustin/Desktop/IsleMesh/openwrt-router/tests/` - Test scripts

### Configuration
- `~/.isle-config.yml` - CLI configuration

## Related Documentation

### Router Details
- `openwrt-router/README.md` - Router architecture
- `openwrt-router/QUICKSTART.md` - Quick start
- `openwrt-router/PHYSICAL-ARCHITECTURE.md` - Network diagram
- `AUTOMATED-ROUTER-SETUP.md` - Automation details

### Project Overview
- `README.md` (in project root)

## Getting Help

1. **Quick answers**: Use [QUICK-REFERENCE.md](QUICK-REFERENCE.md)
2. **Understanding**: Read [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)
3. **Extending**: Check [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md)
4. **Deep dive**: Review [CLI-EXPLORATION-SUMMARY.txt](CLI-EXPLORATION-SUMMARY.txt)
5. **Specific features**: Check feature documentation (DISCOVER.md, etc.)

## Documentation Status

All documentation created: **November 1, 2024**

- [x] Architecture documentation
- [x] Extension guide with examples
- [x] Quick reference
- [x] Summary overview
- [x] Command index
- [x] Feature documentation

## Next Steps

1. Review [QUICK-REFERENCE.md](QUICK-REFERENCE.md) to understand CLI
2. Try commands: `isle help`, `isle router help`, `isle discover`
3. For extensions, follow [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md)
4. For deep understanding, read [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md)

---

**Version**: 1.0
**Created**: November 1, 2024
**Author**: Exploration by Claude Code
**Location**: `/home/dustin/Desktop/IsleMesh/isle-cli/`
