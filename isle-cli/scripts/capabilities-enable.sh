#!/bin/bash
# Local host capability setup, not app deployment or remote administration.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-}"
PLAN=$(python3 "$DIR/capability_profiles.py" "$PROFILE")
python3 -c 'import json,sys; p=json.load(sys.stdin); sys.exit(0 if p["supported"] else p["reason"])' <<< "$PLAN"
mapfile -t PACKAGES < <(python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["packages"]))' <<< "$PLAN")
# Empty package list must not become a blank APT argument.
[[ ${PACKAGES[0]:-} ]] || PACKAGES=()
SERVICE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["service"])' <<< "$PLAN")
[[ $EUID == 0 ]] || { echo 'Run with sudo to prepare THIS device.' >&2; exit 1; }
if [[ "$PROFILE" == hardware-vm && ! -e /dev/kvm ]]; then
    echo 'KVM is not available. Check CPU/firmware virtualization before installing VM hosting capabilities.' >&2; exit 1
fi
if (( ${#PACKAGES[@]} )); then
    echo "Installing missing profile dependencies: ${PACKAGES[*]}"
    bash "$DIR/apt-install.sh" "${PACKAGES[@]}"
else
    echo 'Profile dependencies already present; retaining the existing providers.'
fi
[[ -z "$SERVICE" ]] || systemctl enable --now "$SERVICE"
echo 'Capability packages installed on this device. Individual apps still need resource and hardware checks.'
REPORT=$(mktemp); trap 'rm -f "$REPORT"' EXIT
python3 "$DIR/capabilities.py" status --publish https://api.polari.isle > "$REPORT" || {
    echo 'Local setup finished; report publication needs a retry.' >&2; exit 1;
}
python3 - "$PROFILE" "$REPORT" "$DIR" <<'VERIFY'
import json,sys
sys.path.insert(0,sys.argv[3])
from capability_profiles import PROFILES
required={name:p['required'] for name,p in PROFILES.items()}
facts=json.load(open(sys.argv[2]))['capability_evidence']['facts']
missing=[key for key in required[sys.argv[1]] if facts.get(key,{}).get('status')!='observed' or facts[key].get('value') is not True]
if missing: sys.exit('Packages installed; setup still requires: '+', '.join(missing)+'. Check Isle membership/trust before installing apps.')
print('Installable: baseline capabilities verified on this device.')
VERIFY
