#!/bin/bash
# Local host capability setup, not app deployment or remote administration.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-}"
case "$PROFILE" in
    mesh-app|polari-instance) PACKAGES=(docker.io docker-compose-v2); SERVICE=docker ;;
    hardware-vm) PACKAGES=(qemu-system-x86 libvirt-daemon-system libvirt-clients); SERVICE=libvirtd ;;
    shell-app) PACKAGES=(polari-shell-core); SERVICE= ;;
    *) echo 'Choose mesh-app, polari-instance, hardware-vm or shell-app. Other app types require app-specific capabilities.' >&2; exit 2 ;;
esac
[[ $EUID == 0 ]] || { echo 'Run with sudo to prepare THIS device.' >&2; exit 1; }
if [[ "$PROFILE" == hardware-vm && ! -e /dev/kvm ]]; then
    echo 'KVM is not available. Check CPU/firmware virtualization before installing VM hosting capabilities.' >&2; exit 1
fi
bash "$DIR/apt-install.sh" "${PACKAGES[@]}"
[[ -z "$SERVICE" ]] || systemctl enable --now "$SERVICE"
echo 'Capability packages installed on this device. Individual apps still need resource and hardware checks.'
REPORT=$(mktemp); trap 'rm -f "$REPORT"' EXIT
python3 "$DIR/capabilities.py" status --publish https://api.polari.isle > "$REPORT" || {
    echo 'Local setup finished; report publication needs a retry.' >&2; exit 1;
}
python3 - "$PROFILE" "$REPORT" <<'VERIFY'
import json,sys
required={'mesh-app':['runtime.docker.usable','runtime.compose.usable','agent.running'], 'polari-instance':['runtime.docker.usable','runtime.compose.usable','agent.running'], 'hardware-vm':['runtime.libvirt.usable','runtime.kvm.usable'], 'shell-app':['runtime.shell.installed','access.polari-frontend.https']}
facts=json.load(open(sys.argv[2]))['capability_evidence']['facts']
missing=[key for key in required[sys.argv[1]] if facts.get(key,{}).get('status')!='observed' or facts[key].get('value') is not True]
if missing: sys.exit('Packages installed; setup still requires: '+', '.join(missing)+'. Check Isle membership/trust before installing apps.')
print('Installable: baseline capabilities verified on this device.')
VERIFY
