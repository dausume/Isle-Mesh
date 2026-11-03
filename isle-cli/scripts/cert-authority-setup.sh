#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
[[ $EUID == 0 ]] || { echo 'Run on the Isle core with sudo.'; exit 1; }
[[ $(cat /etc/isle-mesh/agent/agent.mode) == core ]] || exit 1
GATEWAY=$(docker network inspect isle-agent-net --format '{{(index .IPAM.Config 0).Gateway}}')
python3 - "$GATEWAY" <<'PY'
import ipaddress,sys
assert ipaddress.ip_address(sys.argv[1]).is_private
PY
if [[ ${1:-} == serve ]]; then
    exec python3 "$SCRIPT_DIR/cert-authority.py" serve --bind "$GATEWAY"
fi
systemctl daemon-reload
install -d -m 700 /etc/isle-mesh/ca/enrollment
systemctl enable --now isle-cert-authority.service
bash "$SCRIPT_DIR/certs.sh" ensure certs.isle
bash "$SCRIPT_DIR/../../isle-agent/scripts/agent-manager.sh" register \
    --name isle-cert-authority --domain certs.isle --container "$GATEWAY" --port 9849
bash "$SCRIPT_DIR/hosts-reconcile.sh"
source "$SCRIPT_DIR/agent-address.sh"
IP=$(isle_agent_address)
isle dns register certs.isle "$IP"
bash "$SCRIPT_DIR/certs.sh" reload
curl --fail --silent --show-error --max-time 15 https://certs.isle/health
printf '\nCertificate enrollment service ready. Pair each member once using isle certs authorize-member.\n'
