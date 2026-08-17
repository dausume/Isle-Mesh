#!/bin/bash
# push-to-polari.sh — the isle feeds ITSELF into its polari
# (mac-5 seam, v1): device facts + agent registry + nginx fragments
# POSTed to prf-isle over the agent (api.polari.isle). REAL data
# only — this script never writes the mock_network flag.
set -u
API="https://api.polari.isle"
CURL="curl -sk --resolve api.polari.isle:443:127.0.0.1"
# the device's CANONICAL isle name (topology identity) — falls back to
# the hostname; isle create/core-install may write the canonical file so
# a machine's isle identity survives hostname quirks
HOST=$(cat /etc/isle-mesh/canonical-name 2>/dev/null || hostname)

# device facts + uplinks
LINKS=$(ip -br link | awk '$1!~/^(lo|veth|br-|docker|virbr|isle-br)/ {print $1" "$2}')
AGENT=$(docker ps --format "{{.Names}}" | grep -cE "^isle(-vlan)?-agent$" || true)
ROUTER=$(sudo -n virsh list --state-running 2>/dev/null | grep -c isle-router || true)
LINKS="$LINKS" python3 - "$HOST" "${AGENT:-0}" "${ROUTER:-0}" << "PYEOF" | $CURL -X POST -H "Content-Type: application/json" --data-binary @- "$API/api/islemesh/ingest/device" > /dev/null
import json, os, sys
host, agent, router = sys.argv[1], sys.argv[2].strip(), sys.argv[3].strip()
uplinks = []
for line in os.environ.get("LINKS", "").splitlines():
    p = line.split()
    if len(p) < 2: continue
    kind = "ethernet" if p[0].startswith("e") else ("wifi" if p[0].startswith("w") else "")
    if kind: uplinks.append({"interface": p[0], "kind": kind, "link_up": p[1] == "UP"})
import os.path
entry = os.path.exists("/etc/isle-mesh/entrypoint.enabled")
doors = []
try:
    led = json.load(open("/etc/isle-mesh/exposures.json"))
    doors = list(led.get("exposures", {}).values())
except Exception:
    pass
print(json.dumps({"device": host, "facts": {"machine_name": host,
  "agent_present": agent not in ("", "0"),
  "hosts_router": router not in ("", "0"),
  "router_running": router not in ("", "0"),
  "connectivity_mode": "dual-home", "is_entrypoint": entry,
  "notes": "pushed by the isle itself (push-to-polari.sh)"},
  "uplinks": uplinks, "exposures": doors}))
PYEOF

# registry
if [ -r /etc/isle-mesh/agent/registry.json ]; then
  python3 - "$HOST" << "PYEOF" | $CURL -X POST -H "Content-Type: application/json" --data-binary @- "$API/api/islemesh/ingest/registry" > /dev/null
import json, sys
print(json.dumps({"device": sys.argv[1],
  "registry": json.load(open("/etc/isle-mesh/agent/registry.json"))}))
PYEOF
fi

# fragments
CONF=/etc/isle-mesh/agent/nginx/configs
if [ -d "$CONF" ]; then
  python3 - "$HOST" "$CONF" << "PYEOF" | $CURL -X POST -H "Content-Type: application/json" --data-binary @- "$API/api/islemesh/ingest/fragments" > /dev/null
import json, os, sys
host, conf = sys.argv[1], sys.argv[2]
frags = {}
for f in sorted(os.listdir(conf)):
    if f.endswith(".conf"):
        frags[f] = open(os.path.join(conf, f)).read()
print(json.dumps({"device": host, "fragments": frags}))
PYEOF
fi

# engine declarations (isle app deploy --engine): each app dir may
# hold engine.json (provides + url) — push so polari wires the app
# as a provider (odoo -> OdooInstanceConfig.base_url, etc).
for ej in /etc/isle-mesh/apps/*/engine.json; do
  [ -f "$ej" ] || continue
  APP=$(basename "$(dirname "$ej")")
  python3 - "$APP" "$ej" "$HOST" <<'PYEOF2' | $CURL -X POST -H "Content-Type: application/json" --data-binary @- "$API/api/islemesh/ingest/engine" > /dev/null
import json, sys
app, path, host = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path))
print(json.dumps({"device": host, "app": app,
  "provides": d.get("provides", ""), "url": d.get("url", "")}))
PYEOF2
done

echo "pushed $(date -Iseconds)"

# CORE-relayed .isle DNS for REMOTE-hosted apps (reported instances
# x router agent leases; remotes cannot register router DNS)
bash /usr/share/isle-mesh/isle-cli/scripts/dns-reconcile.sh || true

# network resource ledger (pools + published ports) — keeps the
# topology's collision assessments live (Dustin: track subnets/ports
# so apps/engines scale without conflict)
bash /usr/share/isle-mesh/isle-cli/scripts/net.sh report >/dev/null 2>&1 || true
