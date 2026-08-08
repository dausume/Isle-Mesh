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
