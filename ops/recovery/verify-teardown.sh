#!/usr/bin/env bash
# verify-teardown.sh — after `sudo isle destroy --purge --force`, check that every
# artifact the teardown-completeness audit flagged is actually gone. Read-only.
#   Run:  bash ~/Isle-Mesh/verify-teardown.sh
pass=0; fail=0
chk(){ # chk "label" "test-cmd..."  -> PASS if cmd FAILS (artifact absent)
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  FAIL  $label (still present)"; fail=$((fail+1))
  else echo "  ok    $label (gone)"; pass=$((pass+1)); fi
}
echo "== systemd units (should all be gone/disabled) =="
for svc in isle-mesh-boot isle-host-agent mesh-mdns agent-registry-sync isle-device-relay isle-port-detection; do
  chk "service $svc" bash -c "systemctl cat $svc"
done
echo "== udev + boot artifacts =="
chk "90-isle-hotplug.rules"        test -e /etc/udev/rules.d/90-isle-hotplug.rules
chk "99-isle-mesh-ports.rules"     test -e /etc/udev/rules.d/99-isle-mesh-ports.rules
echo "== helper binaries + scripts =="
chk "/usr/local/bin/isle-mesh"     test -e /usr/local/bin/isle-mesh
chk "agent-registry-watcher"       test -e /usr/local/bin/agent-registry-watcher
chk "sync-lh-mdns-and-agent-registry" test -e /usr/local/bin/sync-lh-mdns-and-agent-registry
chk "mesh-mdns.conf"               test -e /usr/local/etc/mesh-mdns.conf
echo "== NetworkManager isle-cable profiles =="
chk "NM isle-cable-* profiles"     bash -c "nmcli -t -f NAME connection show 2>/dev/null | grep -q '^isle-cable-'"
echo "== state / logs / config tree =="
chk "/etc/isle-mesh"               test -e /etc/isle-mesh
chk "/var/lib/isle-mesh"           test -e /var/lib/isle-mesh
chk "/var/log/isle-hotplug.log"    test -e /var/log/isle-hotplug.log
echo "== docker + router =="
chk "isle docker networks"         bash -c "docker network ls --format '{{.Name}}' 2>/dev/null | grep -qE 'isle-br-0|isle-agent-net|isle-remote-macvlan'"
chk "isle containers"              bash -c "docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qE 'isle-vlan-agent|isle-sample-app|isle-remote-agent'"
chk "openwrt router VM"            bash -c "virsh -c qemu:///system list --all 2>/dev/null | grep -q openwrt-isle"
echo "== isle CLI is KEPT (this should say PRESENT) =="
if command -v isle >/dev/null 2>&1; then echo "  ok    isle CLI present (kept, as intended)"; else echo "  NOTE  isle CLI gone (only expected if you also ran 'isle uninstall')"; fi
echo
echo "==== teardown: $pass clean / $fail leftover ===="
[ "$fail" -eq 0 ] && echo "CLEAN — teardown caught everything the audit flagged." || echo "INCOMPLETE — $fail artifact(s) remain (see FAIL lines)."
