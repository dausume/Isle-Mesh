# Notes from pol-core Claude (convergence arc, mac-*)

Working agreement (Dustin, 2026-08-07): pol-core Claude has full
responsibility over SSH for the app-layer merge, in active
communication with THIS machine Claude. This file is the running
channel — append, do not rewrite. Sudo grant: /etc/sudoers.d/isle-claude.

## 2026-08-07/08 ops log (what changed on this machine)

- Bring-up: ~/isle-bringup-mac2.sh (bringup + boot persistence +
  grant). Router VM up, bridges up, isle-mesh-boot.service NOW
  INSTALLED (was missing — isle down after every reboot).
- 🐛 isle-vlan-agent crash-loop (114 restarts): nginx emerg "host
  not found in upstream isle-sample-app:5000" — the agent container
  was attached ONLY to isle-br-0 (macvlan) while apps live on
  isle-agent-net, so docker DNS could not resolve upstreams. The
  live agent compose DECLARES both networks; the running container
  was created by a path that attached only one. LIVE FIX: docker
  network connect isle-agent-net isle-vlan-agent (persists across
  restarts, NOT recreation — fold into agent-manager/bringup).
- 🔑 THE PORT COLLISION (the convergence lesson): dockerd bound
  *:80/*:443 on THIS machine because polari swarm published its
  proxy via INGRESS MESH (binds on every node). Resolution agreed
  with the architecture: the DEVICE ingress belongs to isle-agent;
  polari prf-proxy now publishes mode=host on pol-core only.
  ⚠ gotcha: worker ingress state went STALE (kept 80/443 after the
  service update + docker restart) — a manager-side
  service update --force re-propagated it.
- Agent now healthy on BOTH networks; https://sample.local → 200.

## Coming next (plan cut pending w/ Dustin)
polari deployed ON the isle as an isle-app (the 2026-07-03 shared
plan §3): containers on this machine, ingress ONLY via the agent
(polari.isle), no home-LAN exposure. The compose→isle-app scaffold
pipeline is the intended path (dogfood). An agent-side pusher will
feed registry/fragments/device facts into polari (replacing pol-core
SSH pulls). Plans live in polari-suite: MESH_APP_CONVERGENCE_*.md.

## 2026-08-08 — polari deployed ON the isle (prf-isle)

- ~/polari-isle/: compose (backend+frontend :staging, NO ports,
  isle-agent-net, sqlite, lean/no-KC) + runtime-config +
  push-to-polari.sh + systemd timer polari-isle-push (2min).
- Registered via agent-manager.sh register as TWO apps (polari →
  prf-isle-frontend:4200, polari-api → prf-isle-backend:3000) —
  register is single-service-per-app; the registry schema supports
  services[] w/ subdomains but no verb fills it (converter-upgrade
  candidate). Certs: /etc/isle-mesh/agent/ssl/{certs,keys}/
  <domain>.{crt,key} self-signed.
- .isle DNS: polari.isle + api.polari.isle → 10.10.0.2 (agent
  macvlan) via isle dns register.
- Gotchas for this side: staging frontend nginx answers on 4200;
  pusher needs sudo -n for virsh (router detection).
- The agent now fronts polari; polari ingests this machine
  registry/fragments/facts every 2min and its /isle-mesh page
  draws the isle from inside it.

## 2026-08-08 — .isle certs now CA-SIGNED (suite Polari Root CA)
agent ssl slots for polari.isle + api.polari.isle now hold a
FULLCHAIN wildcard leaf (*.isle, *.polari.isle; 1yr) issued by the
polari suite CA — one root import in a browser = all isle apps
green. Convention going forward: new .isle apps can copy this
wildcard fullchain into their <domain>.crt slot instead of
self-signing (or the fragment generator learns a default-cert
path). Reload that works in the vlan-agent: kill -HUP 1.
