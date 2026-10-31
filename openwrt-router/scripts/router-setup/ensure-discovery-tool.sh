#!/usr/bin/env bash
# BusyBox nc in the pristine router cannot broadcast UDP. Use the inventoried socat IPK.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../utilities/add-ethernet-connection-lib/45-openwrt-network.sh"
_router_probe || exit 1
if _router_ssh 'command -v socat' >/dev/null; then exit 0; fi
MEDIUM=$(cat /etc/polari/offline-source)
IPK=$(python3 - "$MEDIUM" <<'PY'
import sys,json,pathlib,hashlib
root=pathlib.Path(sys.argv[1]);e=json.loads((root/'router/build.json').read_text())['packages']['socat']
assert pathlib.Path(e['file']).name==e['file']
p=root/'router/packages'/e['file']
assert hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256'], 'socat archive checksum mismatch'
print(p)
PY
)
# Transfer through the already-authenticated channel; never evaluate downloaded code on the host.
_router_ssh 'cat > /tmp/isle-discovery-socat.ipk' < "$IPK"
_router_ssh 'opkg install /tmp/isle-discovery-socat.ipk && rm /tmp/isle-discovery-socat.ipk && command -v socat'
