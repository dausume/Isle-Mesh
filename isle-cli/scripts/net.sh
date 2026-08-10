#!/bin/bash
# net.sh — `isle net`: the NETWORK RESOURCE view (Dustin) — what
# subnets/pools and host ports THIS device has allocated, so apps/
# engines can be added at near-arbitrary scale without collision.
#
#   isle net status          docker pools + published ports here
#   isle net report          POST them to the isle topology (facts)
#   isle net free-subnet     a /24 that overlaps nothing here
#   isle net free-port       a host port not already published here
#
# Pure observation + suggestion; allocation stays with the deploy
# verbs (which consult this). The collector is shared by
# push-to-polari so the topology's conflict assessments stay live.
set -u
G="\033[0;32m"; Y="\033[1;33m"; N="\033[0m"
API="https://api.polari.isle"

# emit {"pools":[{name,cidr}],"ports":[{port,container}]} for THIS host
collect(){
    python3 - <<'PYEOF'
import json, subprocess
def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True,
                              timeout=10).stdout
    except Exception:
        return ""
pools = []
for name in sh("docker","network","ls","--format","{{.Name}}").split():
    out = sh("docker","network","inspect",name,"--format",
             "{{range .IPAM.Config}}{{.Subnet}} {{end}}")
    for cidr in out.split():
        if "/" in cidr:
            pools.append({"name": name, "cidr": cidr})
ports = []
# published host ports from running containers
ins = sh("docker","ps","--format","{{.Names}}|{{.Ports}}")
for line in ins.splitlines():
    if "|" not in line:
        continue
    cname, portspec = line.split("|", 1)
    for chunk in portspec.split(","):
        chunk = chunk.strip()
        # forms like 0.0.0.0:18080->80/tcp
        if "->" in chunk and ":" in chunk.split("->")[0]:
            hostpart = chunk.split("->")[0]
            hp = hostpart.rsplit(":", 1)[-1]
            if hp.isdigit():
                ports.append({"port": int(hp), "container": cname})
# dedup by (port, container): the SAME container on v4+v6 is one
# door, not a conflict; DIFFERENT containers on one port stay (a
# real conflict the ledger must flag)
seen, dports = set(), []
for p in ports:
    key = (p["port"], p["container"])
    if key in seen:
        continue
    seen.add(key); dports.append(p)
print(json.dumps({"pools": pools, "ports": dports}))
PYEOF
}

case "${1:-status}" in
    collect) collect ;;
    status)
        collect | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("docker pools on this device:")
for p in d["pools"]:
    print("  %-34s %s" % (p["name"], p["cidr"]))
print("published host ports:")
if not d["ports"]:
    print("  (none)")
for p in d["ports"]:
    print("  :%-6s %s" % (p["port"], p["container"]))'
        ;;
    report)
        HOST=$(hostname | sed "s/dustin-etts-mesh-core/isle-core/")
        RPT=$(mktemp)
        printf '{"device":"%s","facts":{"machine_name":"%s"},"net":%s}' \
            "$HOST" "$HOST" "$(collect)" > "$RPT"
        # a temp FILE, not a pipe: an @- pipe drains on the first
        # (host-isolated) attempt, leaving the fallback an empty body
        if curl -skf --max-time 8 -X POST -H "Content-Type: application/json" --data-binary @"$RPT" "$API/api/islemesh/ingest/device" >/dev/null 2>&1 \
           || curl -skf --max-time 8 --resolve api.polari.isle:443:127.0.0.1 -X POST -H "Content-Type: application/json" --data-binary @"$RPT" "$API/api/islemesh/ingest/device" >/dev/null 2>&1; then
            echo -e "${G}[ OK ]${N} reported pools+ports to the isle topology"
        else
            echo -e "${Y}[WARN]${N} report skipped (api unreachable)"
        fi
        rm -f "$RPT"
        ;;
    free-subnet)
        collect | python3 -c '
import json, sys
sys.path.insert(0, "/usr/share/isle-mesh/isle-cli")
d = json.load(sys.stdin)
# inline free_subnet (avoid importing the framework on a host)
def rng(c):
    ip,b=c.split("/"); n=int(b)
    v=0
    for i,o in enumerate(ip.split(".")): v|=int(o)<<(24-8*i)
    m=0 if n==0 else ((0xffffffff<<(32-n))&0xffffffff)
    return (v&m,m)
def ov(a,b):
    (na,ma),(nb,mb)=rng(a),rng(b); m=ma&mb; return (na&m)==(nb&m)
pools=[p["cidr"] for p in d["pools"] if "/" in p.get("cidr","")]
for s in range(22,250):
    c="172.%d.0.0/24"%s
    if not any(ov(c,p) for p in pools): print(c); break'
        ;;
    free-port)
        collect | python3 -c '
import json, sys
d = json.load(sys.stdin)
taken={p["port"] for p in d["ports"]}
for pt in range(18080,18999):
    if pt not in taken: print(pt); break'
        ;;
    *) echo "usage: isle net [status|report|free-subnet|free-port]" ;;
esac
