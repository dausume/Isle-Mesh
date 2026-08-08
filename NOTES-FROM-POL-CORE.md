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

## 2026-08-08 — new verb: isle trust (CA as an install step)
trust.sh + dispatch entry (also synced to /usr/share/isle-mesh).
status = detect trust everywhere (system store, Chrome NSS,
Firefox note, LIVE PROBE via --cacert fetch); install = consented
import (fingerprint shown, --yes for postinst), system +
user-NSS, installs libnss3-tools if needed; cert = path+fp.
Root lives at /etc/isle-mesh/ca/isle-root.crt (currently the
polari suite root). INTENDED WIRING: .deb postinst calls
"isle trust install" behind a debconf consent; a plain-HTTP
trust.isle page does the phone walkthrough (JS probe: fetch an
https .isle URL, catch = untrusted). RECOMMENDATION recorded: mint
a DEDICATED name-constrained isle root (permitted DNS=.isle) so
the import can never vouch for non-isle names.

## 2026-08-08 — trust lifecycle COMPLETE (Dustin rulings applied)
Rulings: the app/agent PERFORMS trust actions; CA install is part
of CONNECTING TO THE CORE; auto-update capability; the JavaFX
manager app prompts for elevation (pkexec) so the user consents in
a familiar OS dialog.
Built: trust.isle static page+container (probe JS, per-platform
walkthrough, root download — the phone path); isle trust grew
fetch (first-join: fingerprint = the consent, --fingerprint for
scripted/app-driven joins) + update (SIGNED-CHANNEL RULE: new root
only over TLS the current root authenticates; re-key = explicit
re-consent) + daily isle-trust-update.timer. 🔑 X.509 GOTCHA:
single-label wildcards (*.isle) are REJECTED by OpenSSL — every
top-level .isle app needs an explicit SAN; shared leaf reissued w/
trust.isle (registration-triggered leaf issuance = converter item).
JOIN-FLOW WIRING (to build): manager-app connect → agreement/QR
carries the root fingerprint → app runs pkexec isle trust fetch
--fingerprint <fp> → device fully trusted at join, zero terminal.

## 2026-08-08 — isle certs: registration-triggered leaf issuance
Dustin ruled: wildcards GONE, explicit SANs only. New verb
`isle certs status|sync|issue <domain>`: per-domain EC leaves
signed by the isle INTERMEDIATE (/etc/isle-mesh/ca/signing — the
ROOT key never leaves the suite CA), fullchain into agent slots,
agent HUP. agent-manager register now calls certs issue (the
hook). sync = idempotent reconcile over registry.json (domains +
subdomain.domain). All five registered domains reissued as
individual explicit-SAN leaves. 🔧 gotchas: process substitution
extfile dies under sudo (use a real file); tmp must be USER-owned
for shell redirects; installed CLI at /usr/share needs sudo cp +
chmod 755 after repo edits.

## 2026-08-08 — isle app deploy: the store install pipeline
`isle app deploy <name> --compose <f> [--service --port --domain
--protocol --engine k=urltmpl]`: arbitrary compose -> ONE command
-> running (isle-overlay.yml auto-attaches every service to
isle-agent-net) -> agent register -> leaf issued (hook) -> .isle
DNS -> in the graph. PROVEN with traefik/whoami: cert issued
(DNS:whoami.isle chained to isle intermediate), https://whoami.isle
served, graph edge drawn. --engine writes /etc/isle-mesh/apps/
<name>/engine.json (provides + url) = polari provider-wiring
material. undeploy tears down. Single primary service (registry
shape) = the recorded multi-service gap.

## 2026-08-08 — engine interconnect (§20.4): app deploy --engine
push-to-polari.sh now also POSTs each /etc/isle-mesh/apps/*/
engine.json to /api/islemesh/ingest/engine. Polari side: IsleEngine
row + a BINDER (islemesh_engines.py) that wires known kinds to
their consumer — business-ops/odoo -> OdooInstanceConfig.base_url
(unit-proven: real config row written at the isle url). Unknown
kind or absent consumer module = recorded available-but-unbound,
named honestly (binds when the module lands). So: isle app deploy
odoo --engine business-ops=http://<c>:8069 makes odoo a polari
engine automatically. GET /api/islemesh/engines lists them.

## 2026-08-08 — the general isle app store (§20.1/§20.3): isle store
Catalog lives in polari (IsleCatalogEntry rows + install-plan at
/api/islemesh/catalog[/{entry}]); `isle store list|show|install`
browses it and RUNS the plan on the host (mover-on-host). PROVEN:
store list shows 3 (polari/whoami/odoo across both variants);
store install whoami --yes → catalog plan → isle app deploy →
container+cert+DNS, fully automatic. app-deploy gained --image
(synthesizes a one-service compose). Store dispatches: mesh-app →
isle app deploy; polari-app → shared-shell launcher build+apt;
polari-module → module deb. Consumers of both proven variants, one
front door.

## 2026-08-08 — isle-oriented dev route + full teardown (§25.2/3)
Two host capabilities (in /usr/local/bin, sourced in isle-cli/
scripts):
- isle-polari-teardown [--keep-data|--apps-only]: FULL reproducible
  teardown — store apps (compose down + unregister + cert/DNS drop
  + rm apps dir), prf-isle (compose down -v), polari.isle/api
  deregistered, pusher timer disabled, agent reloaded. Device-level
  (agent, trust-page, CA, sample) correctly persist. PROVEN clean.
- isle-polari-deploy [--modules csv]: deploy/redeploy polari on the
  isle — compose up (POLARI_ISLE_MODULES env), register both
  domains (leaf hook), DNS, pusher, verify health+web. PROVEN.
THE DEV LOOP (main deployment route going forward): on pol-core
`pol node build backend` → `docker save | ssh isle-core docker
load` → `ssh isle-core isle-polari-deploy`. Ran it fully:
teardown→build→ship→deploy→verify; prf-isle came back with fresh
code (catalog --image fix visible). This is how we deploy polari
now — through the isle, not host ports.

## 2026-08-08 — mesh-local docker registry (§10 gap #8 / mac-3)
isle-registry-setup.sh (isle CA host): registry:2 on :5000 with an
isle-CA-signed multi-SAN cert (registry.isle + hostname + home IP
192.168.0.24 + isle IP 192.168.1.254 + localhost); trusted via
/etc/docker/certs.d/<addr>/ca.crt (the isle root); DNS registry.isle
-> 192.168.1.254 (isle-core br-mgmt, where :5000 publishes — NOT the
agent 10.10.0.2). PROVEN push/pull round-trip both registry.isle:5000
and 192.168.0.24:5000. This is the dev-loop accelerator + offline-
complete + swarm-placement enabler.
DEV LOOP now (registry, not save|ssh|load): pol-core `pol node build
backend` -> `docker tag prf-backend:staging 192.168.0.24:5000/
prf-backend:staging` -> `docker push ...` -> `ssh isle-core
isle-polari-deploy --pull`. isle-polari-deploy --pull pulls+retags
from registry.isle:5000 then deploys.
ONE-TIME pol-core trust (Dustin sudo): mkdir -p /etc/docker/certs.d/
192.168.0.24:5000 && cp isle-root.crt there.
