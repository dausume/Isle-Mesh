#!/bin/bash
# hosts-reconcile.sh — CORE hairpin pins for core-served .isle domains.
#
# The core HOST cannot reach its own agent's macvlan addresses
# (kernel macvlan host isolation), so every .isle domain SERVED BY
# THIS CORE must pin to 127.0.0.1 in /etc/hosts or host-local
# clients time out. Instances of this wall found one at a time:
# apt.isle (apt-repo.sh, 2026-08-09), polari.isle (the store shell,
# 2026-08-14), api.polari.isle (dns-reconcile's own --resolve
# fallback). This script makes it a RULE: it owns a marker-delimited
# block and reconciles it from the catalog (domains whose instance
# device == this host) plus the structural baselines — the exact
# complement of dns-reconcile.sh, which handles device != here.
#
# Runs from dns-reconcile.sh (self-feed timer, every 2 min) and from
# core-install. Idempotent; only ever touches its own block; NEVER
# runs on member devices (the router key is the core's mark).
set -u

# the router DIR is the core mark (the key file inside is root-only,
# and this guard must answer honestly for unprivileged runs too)
[ -d /etc/isle-mesh/router ] || exit 0

API="https://api.polari.isle"
CURL="curl -skf --max-time 8"
HOSTN=$(hostname | sed "s/dustin-etts-mesh-core/isle-core/")

CAT=$($CURL "$API/api/islemesh/catalog" 2>/dev/null) \
    || CAT=$($CURL --resolve api.polari.isle:443:127.0.0.1 \
             "$API/api/islemesh/catalog" 2>/dev/null) \
    || CAT="{}"

# CAT rides the environment (the dns-reconcile stdin gotcha).
DOMS=$(CAT="$CAT" HOSTN="$HOSTN" python3 - <<'PYEOF'
import json, os
doms = {"polari.isle", "api.polari.isle"}  # structural: THE core
try:
    d = json.loads(os.environ.get("CAT", "{}"))
except Exception:
    d = {}
for e in d.get("entries", []):
    for i in e.get("instances", []):
        dom = i.get("domain", "")
        if i.get("device", "") == os.environ["HOSTN"] \
                and dom.endswith(".isle"):
            doms.add(dom)
print(" ".join(sorted(doms)))
PYEOF
)
[ -n "$DOMS" ] || exit 0

BEGIN="# BEGIN isle-mesh core hairpin pins (hosts-reconcile owns this block)"
END="# END isle-mesh core hairpin pins"
WANT=$(printf '%s\n127.0.0.1 %s\n%s' "$BEGIN" "$DOMS" "$END")
HAVE=$(sed -n "/^# BEGIN isle-mesh core hairpin pins/,/^# END isle-mesh core hairpin pins/p" /etc/hosts)

[ "$WANT" = "$HAVE" ] && exit 0

TMP=$(mktemp)
sed "/^# BEGIN isle-mesh core hairpin pins/,/^# END isle-mesh core hairpin pins/d" /etc/hosts > "$TMP"
printf '%s\n' "$WANT" >> "$TMP"
if sudo -n cp "$TMP" /etc/hosts 2>/dev/null; then
    echo "hosts-reconcile: pinned core-served domains -> 127.0.0.1: $DOMS"
else
    echo "hosts-reconcile: FAILED (needs root) — wanted: $DOMS"
fi
rm -f "$TMP"
