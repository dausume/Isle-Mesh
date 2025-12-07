# Per-App Mode Architecture

## Overview

Instead of agent-level mode switching (mDNS vs lightweight), the Isle Agent uses **per-app mode configuration**. Each app specifies which modes it's enabled for, and the system dynamically applies appropriate configurations.

## App Modes

Each app can be enabled for one or both modes:

### 1. **Local Mode** (`local`)
- App broadcasts `.local` domains via mDNS
- Accessible on the local network via mDNS discovery
- Used by: `mesh-mdns` system, `isle-host-agent`
- Example: `myapp.local`, `api.myapp.local`

### 2. **Isle Mode** (`isle`)
- App is proxied through nginx reverse proxy (isle-vlan-agent)
- Accessible via the Isle mesh network
- Used by: `isle-agent-sync` generates configs for `isle-vlan-agent`
- Example: nginx routes traffic to app containers

### 3. **Both Modes**
- App is both broadcasted via mDNS AND proxied through nginx
- Maximum accessibility
- Common use case for hybrid deployments

## Registry Structure

**Location**: `/etc/isle-mesh/agent/registry.json`

**Format**:
```json
{
  "domains": {
    "myapp.local": "myapp"
  },
  "subdomains": {
    "api.myapp.local": "myapp",
    "web.myapp.local": "myapp"
  },
  "apps": {
    "myapp": {
      "domain": "myapp.local",
      "services": 2,
      "subdomains": ["api.myapp.local", "web.myapp.local"],
      "modes": ["local", "isle"],  // ← Per-app mode configuration
      "updated_at": "2025-01-15T10:30:00Z"
    }
  }
}
```

## Component Responsibilities

### 1. **isle-host-agent** (Systemd Service)
- **Reads**: Registry to find apps with `"local"` in modes
- **Action**: Broadcasts .local domains via mDNS
- **When**: Startup + watches registry for changes
- **How**:
  - Reads `/etc/isle-mesh/agent/registry.json`
  - Filters apps where `"local" in app["modes"]`
  - Broadcasts each subdomain via Avahi

### 2. **isle-agent-sync** (Python Container)
- **Reads**: Registry to find apps with `"isle"` in modes
- **Action**: Generates nginx config fragments
- **When**: Receives mDNS data OR registry changes
- **How**:
  - Monitors `/etc/isle-mesh/agent/registry.json`
  - Filters apps where `"isle" in app["modes"]`
  - Generates `/etc/isle-mesh/agent/configs/{app}.conf` using Jinja2 templates
  - Triggers nginx reload in isle-vlan-agent

### 3. **isle-vlan-agent** (Nginx Container)
- **Reads**: Nginx config fragments from `/etc/isle-mesh/agent/configs/`
- **Action**: Routes traffic to app containers
- **When**: Config reload triggered by sync agent
- **How**:
  - Includes `/etc/nginx/configs/*.conf` in main config
  - Gracefully reloads when new fragments added

## Workflow

### App Registration

```bash
# App specifies modes in isle-mesh.yml or equivalent
modes: ["local", "isle"]  # Both modes
# OR
modes: ["local"]          # mDNS only
# OR
modes: ["isle"]           # Nginx proxy only
```

### Dynamic Configuration Flow

```
App starts/updates
    │
    ├─→ Registers in /etc/isle-mesh/agent/registry.json
    │   with modes: ["local", "isle"]
    │
    ├─→ IF "local" in modes:
    │   └─→ isle-host-agent detects registry change
    │       └─→ Broadcasts .local domains via mDNS
    │
    └─→ IF "isle" in modes:
        └─→ isle-agent-sync detects registry change
            └─→ Generates nginx config fragment
            └─→ Triggers nginx reload in isle-vlan-agent
                └─→ Traffic now routed through nginx
```

### Example Scenarios

#### Scenario 1: Local Development App
```json
{
  "app_name": "dev-api",
  "modes": ["local"],
  "domain": "dev-api.local"
}
```
- ✅ Broadcasted via mDNS
- ❌ No nginx proxy
- Use case: Local development only

#### Scenario 2: Production Mesh App
```json
{
  "app_name": "prod-service",
  "modes": ["isle"],
  "domain": "prod-service.local"
}
```
- ❌ No mDNS broadcast
- ✅ Nginx proxy enabled
- Use case: Production service in mesh

#### Scenario 3: Hybrid App
```json
{
  "app_name": "hybrid-app",
  "modes": ["local", "isle"],
  "domain": "hybrid.local"
}
```
- ✅ Broadcasted via mDNS (local network)
- ✅ Nginx proxy enabled (mesh network)
- Use case: Accessible both ways

## Configuration Templates

### Local Mode Templates
**Location**: `/home/detts/Isle-Mesh/isle-agent/segments/local/`

Used when generating configs for apps with `"local"` in modes.
- Focuses on .local domain resolution
- mDNS-specific configurations

### Isle Mode Templates
**Location**: `/home/detts/Isle-Mesh/isle-agent/segments/isle/`

Used when generating configs for apps with `"isle"` in modes.
- Focuses on mesh network routing
- VLAN-specific configurations
- Proxy headers for mesh forwarding

## Implementation Status

### ✅ Completed
- [x] Registry structure supports `modes` array
- [x] `generate-app-fragment.py` accepts `--mode` parameter
- [x] Template segments separated by mode (`segments/local/`, `segments/isle/`)
- [x] **Volume mounts**: Registry mounted read-only in both containers
- [x] **Volume mounts**: Templates/segments mounted in sync agent
- [x] **Volume mounts**: Shared `agent-configs` volume between sync and vlan agents

### 🔧 In Progress / To Do
- [ ] **isle-host-agent**:
  - [ ] Read registry and broadcast apps with "local" mode
  - [ ] Watch registry for changes (inotifywait on host)
  - [ ] Generate/update Avahi service files
  - [ ] Reload Avahi on registry changes

- [ ] **isle-agent-sync**:
  - [ ] Watch registry file for changes (watchdog or polling)
  - [ ] Generate nginx configs for apps with "isle" mode
  - [ ] Write configs to `/app/configs` (shared volume)
  - [ ] Trigger nginx reload when configs generated
  - [ ] Add nginx reload mechanism (docker exec or API)

- [ ] **App registration**: Update app startup to set modes in registry
- [ ] **CLI commands**:
  - [ ] `isle app register --modes local,isle`
  - [ ] `isle app set-mode myapp --modes local`

### Nginx Reload Options

Two safe approaches for sync agent to trigger vlan agent reload:

1. **Shared volume sentinel** (RECOMMENDED - Simple & Safe):
   - Sync writes timestamp to `.reload` file in shared volume
   - Vlan sidecar watches for file changes and triggers nginx reload
   - No special privileges needed
   - Clean separation of concerns

   ```python
   # In sync agent - trigger reload
   from pathlib import Path
   from datetime import datetime

   def trigger_nginx_reload():
       reload_file = Path('/app/configs/.reload')
       reload_file.write_text(datetime.now().isoformat())
       print("🔄 Triggered nginx reload")
   ```

   ```bash
   # In vlan agent - watch for reload signal
   #!/bin/sh
   RELOAD_FILE="/etc/nginx/configs/.reload"
   LAST_RELOAD=""

   while true; do
       if [ -f "$RELOAD_FILE" ]; then
           CURRENT=$(cat "$RELOAD_FILE")
           if [ "$CURRENT" != "$LAST_RELOAD" ]; then
               echo "Reloading nginx..."
               nginx -s reload && echo "✓ Nginx reloaded"
               LAST_RELOAD="$CURRENT"
           fi
       fi
       sleep 2
   done
   ```

2. **Network API call** (Alternative - Most Decoupled):
   - Sync calls `POST http://isle-vlan-agent:8080/reload`
   - Vlan runs lightweight sidecar process (Python/Go) that listens and triggers reload
   - Complete network isolation
   - More complex but most robust

   ```python
   # In sync agent
   import requests

   def trigger_nginx_reload():
       try:
           requests.post('http://isle-vlan-agent:8080/reload')
           print("🔄 Triggered nginx reload")
       except Exception as e:
           print(f"Failed to trigger reload: {e}")
   ```

**NEVER** mount Docker socket (`/var/run/docker.sock`) - this creates catastrophic security vulnerabilities!

**Recommendation**: Use shared volume sentinel (option 1) for simplicity and security.

## Volume Mounts for Container Access

Since **isle-agent-sync** and **isle-vlan-agent** run in Docker containers, they need volume mounts to access host configuration:

### isle-agent-sync volumes:
```yaml
volumes:
  # Shared volume for generated nginx configs (write access)
  - agent-configs:/app/configs

  # Read-only access to host registry (monitors for changes)
  - /etc/isle-mesh/agent/registry.json:/app/registry.json:ro

  # Read-only access to template segments for config generation
  - ./segments:/app/segments:ro
  - ./templates:/app/templates:ro
```

### isle-vlan-agent volumes:
```yaml
volumes:
  # Master nginx config
  - ./isle-vlan-agent/nginx.conf:/etc/nginx/nginx.conf:ro

  # App config fragments (read-only, written by sync agent)
  - agent-configs:/etc/nginx/configs:ro

  # Read-only access to host registry (for status/monitoring)
  - /etc/isle-mesh/agent/registry.json:/etc/nginx/registry.json:ro

  # SSL certificates
  - /etc/isle-mesh/agent/ssl:/etc/nginx/ssl:ro
```

## Registry Monitoring

### isle-host-agent (Bash + inotifywait)
**Runs on**: Host (systemd service)
**Watches**: `/etc/isle-mesh/agent/registry.json` (host filesystem)

```bash
#!/bin/bash
# Watch registry and update mDNS broadcasts

REGISTRY="/etc/isle-mesh/agent/registry.json"

update_mdns_broadcasts() {
    # Read registry
    local apps=$(jq -r '.apps | to_entries[] | select(.value.modes | contains(["local"])) | .key' "$REGISTRY")

    # For each app with "local" mode
    for app_name in $apps; do
        local domain=$(jq -r ".apps.\"$app_name\".domain" "$REGISTRY")
        local subdomains=$(jq -r ".apps.\"$app_name\".subdomains[]" "$REGISTRY")

        # Update Avahi service files
        generate_avahi_service "$app_name" "$domain" "$subdomains"
    done

    # Reload Avahi
    systemctl reload avahi-daemon
}

# Initial update
update_mdns_broadcasts

# Watch for changes (inotifywait monitors host filesystem)
while inotifywait -e modify "$REGISTRY"; do
    echo "Registry changed, updating mDNS broadcasts..."
    update_mdns_broadcasts
done
```

### isle-agent-sync (Python + watchdog)
**Runs in**: Docker container
**Watches**: `/app/registry.json` (mounted from host)

```python
import json
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

REGISTRY_PATH = Path('/app/registry.json')
CONFIG_OUTPUT_DIR = Path('/app/configs')
SEGMENTS_DIR = Path('/app/segments')
TEMPLATES_DIR = Path('/app/templates')

class RegistryWatcher(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith('registry.json'):
            print("📝 Registry changed, regenerating nginx configs...")
            self.update_nginx_configs()

    def update_nginx_configs(self):
        # Read registry from mounted volume
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)

        # Process apps with "isle" in modes
        for app_name, app_data in registry['apps'].items():
            if 'isle' in app_data.get('modes', []):
                print(f"  Generating config for {app_name} (isle mode)...")
                self.generate_nginx_config(app_name, app_data)

        # Trigger nginx reload in isle-vlan-agent container
        self.trigger_nginx_reload()

    def generate_nginx_config(self, app_name, app_data):
        """Generate nginx config fragment using templates"""
        # Use Jinja2 to render template from segments/isle/
        # Write to /app/configs/{app_name}.conf (shared volume)
        pass

    def trigger_nginx_reload(self):
        """Signal isle-vlan-agent to reload nginx via sentinel file"""
        from datetime import datetime
        reload_file = Path('/app/configs/.reload')
        reload_file.write_text(datetime.now().isoformat())
        print("🔄 Triggered nginx reload via sentinel file")

# Start watching
observer = Observer()
observer.schedule(RegistryWatcher(), path=str(REGISTRY_PATH.parent), recursive=False)
observer.start()
```

### Alternative: Polling Instead of inotify

For containers, inotify on mounted files can be unreliable. Consider polling:

```python
import json
import time
from pathlib import Path

REGISTRY_PATH = Path('/app/registry.json')
last_modified = 0

def check_registry_changed():
    global last_modified
    current_modified = REGISTRY_PATH.stat().st_mtime

    if current_modified != last_modified:
        last_modified = current_modified
        return True
    return False

while True:
    if check_registry_changed():
        print("Registry changed, updating configs...")
        update_nginx_configs()

    time.sleep(5)  # Check every 5 seconds
```

## Benefits

1. **Flexibility**: Apps choose their own accessibility model
2. **No Agent Mode Switching**: System adapts to app needs automatically
3. **Dynamic Reconfiguration**: Change app modes without restarting agent
4. **Clear Separation**: Each component handles its mode independently
5. **Hybrid Support**: Apps can be in both modes simultaneously

## Next Steps

1. Implement registry monitoring in isle-host-agent
2. Implement registry monitoring in isle-agent-sync
3. Add nginx config generation logic to sync agent
4. Test dynamic mode changes
5. Document app configuration format for setting modes
