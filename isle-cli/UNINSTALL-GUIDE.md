# Isle-Mesh Uninstall Guide

This guide explains how to safely uninstall Isle-Mesh and manage system dependencies.

## Quick Start

### Basic Uninstall (Interactive)
```bash
isle uninstall
```

This will:
- Unlink the Isle CLI from npm
- Prompt you before removing system components
- Prompt you before removing dependencies

### Complete Uninstall
```bash
sudo isle uninstall --all --remove-deps
```

This will remove everything including all dependencies.

### Keep Shared Dependencies
```bash
sudo isle uninstall --all --remove-deps --keep-shared
```

This removes Isle-Mesh but preserves shared utilities like `wget`, `jq`, `yq`, and `avahi-utils`.

## Options

| Option | Description |
|--------|-------------|
| `--all` | Remove all system components (port detection, udev rules, etc.) |
| `--remove-deps` | Remove Isle-Mesh specific dependencies |
| `--keep-shared` | Keep shared dependencies that other apps may use |
| `--help` | Show help message |

## What Gets Removed

### Isle CLI (Always Removed)
- npm global link for `isle` command
- Command-line interface

### System Components (--all)
- No system components currently installed by the CLI

### Isle-Mesh Specific Dependencies (--remove-deps)

These are **safe to remove** if you're not using them for other purposes:

- **qemu-kvm** / **qemu** - Virtualization platform
- **libvirt-daemon-system** / **libvirt** - VM management
- **libvirt-clients** / **virt-install** - VM control tools
- **bridge-utils** - Network bridging tools
- **libvirtd service** - Will be stopped and disabled
- **User permissions** - Removes user from libvirt group

### Shared Dependencies (Prompts Before Removal)

These utilities **may be used by other applications**. The script will prompt before removing them:

- **wget** - File download utility (very common, used by many tools)
- **jq** - JSON processor (commonly used in scripts)
- **yq** - YAML processor (used for config file manipulation)
- **avahi-utils** - mDNS/DNS-SD discovery tools

### NOT Installed by Isle-Mesh (Informational Only)

These are **not removed** by the uninstall script as Isle-Mesh doesn't install them:

- **Docker** - Container platform (used but not installed by Isle-Mesh)
- **Node.js/npm** - JavaScript runtime (required to run Isle CLI)

If you want to remove Docker or Node.js, you must do so manually:
- Docker: https://docs.docker.com/engine/install/
- Node.js: Use your system's package manager

## Safety Features

The uninstall script includes several safety features:

1. **Interactive Prompts** - By default, asks before removing anything beyond the CLI
2. **Shared Dependency Detection** - Checks which shared utilities are installed
3. **Warnings** - Clearly indicates which dependencies might be used by other apps
4. **Sudo Requirements** - Requires sudo only for system-level changes
5. **Package Manager Detection** - Works with apt, yum, dnf, and pacman

## Examples

### Scenario 1: Just Remove the CLI
```bash
# Unlink the CLI but keep everything else
isle uninstall
# Answer "N" to all prompts
```

### Scenario 2: Remove Isle-Mesh but Keep Virtualization
```bash
# Remove CLI and system components, but keep dependencies
sudo isle uninstall --all
# Answer "N" when asked about dependencies
```

### Scenario 3: Complete Clean Uninstall
```bash
# Remove everything including all dependencies
sudo isle uninstall --all --remove-deps
# Follow prompts for shared dependencies
```

### Scenario 4: Automated Uninstall (Keep Shared)
```bash
# Remove everything but automatically preserve shared utilities
# Useful for scripts and automation
sudo isle uninstall --all --remove-deps --keep-shared
```

## Dependency Decision Tree

Not sure whether to remove dependencies? Use this decision tree:

### Isle-Mesh Specific Dependencies (KVM, libvirt, bridge-utils)

**Remove if:**
- You don't use virtual machines
- You don't use other VM management tools (virt-manager, etc.)
- You installed Isle-Mesh specifically for its router feature

**Keep if:**
- You use virt-manager or other libvirt-based tools
- You manage VMs on this machine
- You use other virtualization tools

### Shared Dependencies

#### wget
**Remove if:**
- You never download files from the command line
- No other scripts or tools use it (unlikely)

**Keep if:**
- You download files frequently
- Other scripts depend on it (very common)

#### jq
**Remove if:**
- You don't work with JSON on the command line
- No other scripts use it

**Keep if:**
- You process JSON files or APIs
- Other development tools depend on it

#### yq
**Remove if:**
- You don't work with YAML files
- No other tools use it

**Keep if:**
- You manage Kubernetes, Ansible, or other YAML-heavy tools
- Other configuration scripts need it

#### avahi-utils
**Remove if:**
- You don't use mDNS/Bonjour discovery
- No other network services need it

**Keep if:**
- You use AirPrint, AirPlay, or other mDNS services
- You discover network devices regularly

## Troubleshooting

### "Sudo required" Error

If you see this error, you're trying to remove system components without sudo:

```bash
# Instead of:
isle uninstall --all

# Use:
sudo isle uninstall --all
```

### Package Removal Fails

If a package fails to remove, you may need to remove it manually:

```bash
# For Debian/Ubuntu:
sudo apt-get remove <package-name>

# For RHEL/Fedora:
sudo dnf remove <package-name>

# For Arch:
sudo pacman -R <package-name>
```

### Checking What Depends on a Package

Before removing a shared dependency, check what else might use it:

```bash
# Debian/Ubuntu:
apt-cache rdepends <package-name>

# RHEL/Fedora:
dnf repoquery --whatrequires <package-name>

# Arch:
pactree -r <package-name>
```

## Partial Uninstalls

### Remove Only mDNS Configuration
```bash
isle mdns uninstall
```

## Post-Uninstall Cleanup

After uninstalling, you may want to clean up:

1. **Remove unused packages** (auto-installed dependencies):
   ```bash
   # Debian/Ubuntu:
   sudo apt-get autoremove

   # RHEL/Fedora:
   sudo dnf autoremove

   # Arch:
   sudo pacman -Qdtq | sudo pacman -R --noconfirm -
   ```

2. **Remove configuration files** (if you want a completely clean slate):
   ```bash
   # Check for any remaining Isle-Mesh configs
   find ~ -name "*isle*" -o -name "*mesh*"
   ```

## Reinstalling

If you change your mind, you can reinstall Isle-Mesh:

```bash
# Reinstall CLI
cd /path/to/IsleMesh/isle-cli
npm link

# Reinstall dependencies
sudo isle install all
```

## Getting Help

If you encounter issues:

1. Run with `--help` to see all options:
   ```bash
   isle uninstall --help
   ```

2. Check this guide for troubleshooting steps

3. Report issues at: https://github.com/anthropics/isle-mesh/issues (replace with actual repo URL)

---

**Note**: This uninstall process is designed to be safe and conservative. When in doubt, the script will ask for confirmation before removing anything that might affect other applications.
