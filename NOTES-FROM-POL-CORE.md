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

## 2026-08-10 — dev branch + untracked-helper triage (pol-core session)
- Created **`dev`** at the `dev-consolidation` tip (4056cba); it is
  now the go-forward line (+ ops/recovery + this note). Push:
  `git push -u origin dev`. `dev-consolidation` kept, unchanged.
- Triaged the 4 root untracked helpers:
  - RESERVED to `ops/recovery/` (committed on dev): fix-live-certs.sh
    (cert-repair; no `isle certs repair` verb exists — promote later)
    and verify-teardown.sh (teardown-completeness audit, reusable).
  - RETIRED (still untracked at root, safe to rm — one-time):
    fix-live-registry.sh (relay hardening now in-tree, migration
    obsolete) and RUN-app-install-test.md (dated; references the
    superseded isle-manager-app deb).
- This session's isle CLI work (net ledger, url/expose, polari
  instance/module/app verbs, placement resolver) is on dev, ~39
  commits ahead of origin.

## 2026-09-03 — VPN arc vpn-1: the Polari half is built; the contract for the isle half (pol-core session)
Plan: suite `AI-Notes/plans/VPN_FEDERATION_PLAN.md` (§7 = his review:
authority is the ISLE side, `.vpn` rung, Isle Link / Isle Bridge,
D1–D14 ratified 2026-09-03). Kinds + names: `AI-Notes/guides/VPN_APP_KINDS.md`.
Handoff with your items I-1..I-5: `AI-Notes/handoffs/VPN_ARC_HANDOFF.md`.
Polari side (module `vpn`, branch dev-vpn-1): `modules/vpn/` — mirror
rows VpnNetwork / VpnPeer (public key only) / VpnAccessRule /
VpnFederationLink / AppVpnExposure, inbox VpnProposal, engine
(render Link confs with an `@@DEVICE_PRIVATE_KEY@@` placeholder,
nftables text, Bridge refuses until step-ca), `/api/vpn/*` read +
`POST /api/vpn/proposals`, `/display/vpn`, the ten `isle-vpn`
IsleCatalogEntry rows (gateway kinds `provides_engine vpn-gateway`),
`pol vpn`. Selftest `python3 -m vpn.selftest_vpn` (75 checks) runs
the two-isle flow in-process (`vpn.vpn_demo`).

**THE CONTRACT (agree here before I-4/I-5):**

1. **Push** — `push-to-polari.sh` POSTs to `$API/api/islemesh/ingest/vpn`
   (same envelope discipline as the other ingests: `device` required,
   REAL pushes never write `mock_network`). Replace-per-device: the
   newest push is THE truth for that device's rows.
   ```
   {"device": "<canonical isle name>", "schema_version": "1",
    "app": {"name": "isle-vpn", "kind": "vpn-link-gateway",   # one of the ten kind ids
            "version": "", "config_api": "http://<isle-local addr>:<port>", "status": "up"},
    "networks": [{"network_name", "mode": "mesh|hub|p2p", "cidr", "listen_port",
                  "interface": "wg-arch", "dns_suffix": ".vpn", "mtu",
                  "forward_allowed": false, "masquerade": false,
                  "preshared_default": false, "status": "up|down"}],
    "peers":    [{"network_name", "peer_name", "kind", "public_key", "address",
                  "endpoint", "allowed_ips": [...], "persistent_keepalive_s",
                  "has_preshared": false, "last_handshake": "<iso>",
                  "rx_bytes", "tx_bytes", "status": "active|stale|never",
                  "remote_device": "<isle>"}],   # the isle's OWN entry has remote_device == device
    "rules":    [{"network_name", "name", "from_tag", "to_target", "action": "allow|deny", "ports", "order"}],
    "links":    [{"network_name", "remote_device", "remote_network", "gateway_peer",
                  "remote_cidrs": [...], "agreement_id", "relay_kind": "direct|blind|routing",
                  "status": "pending|active|revoked", "arch_name"}],
    "exposures":[{"app_name", "network_name", "role": "server|user|observer|relay-only", "status"}],
    "proposals":[{"id": "vp-…", "status": "applied|rejected", "applied_by": "<operator>",
                  "applied_at": "<iso>", "note": ""}]}
   ```
   REFUSED WHOLE (HTTP 400, receipt says why) if ANY key named
   private_key / preshared_key / psk / tls_key / ca_key / client_key /
   server_key appears at any depth. Keys never leave the device. A
   gateway-kind `app.kind` makes Polari write the IsleEngine row
   `provides: vpn-gateway` for the device — that is what turns the
   `.vpn` rung on (`GET /api/vpn/exposure-options?device=<isle>`);
   you may ALSO ship `engine.json {"provides": "vpn-gateway"}` in the
   app dir like odoo does — both land on the same row.
2. **Proposals** — `isle vpn apply <id>` pulls
   `GET /api/vpn/proposals?device=<isle>&status=proposed` (or
   `GET /api/vpn/proposals/<id>`): `{name, device_name, kind:
   network|peer|rule|link|exposure|revoke, provider, app_kind,
   network_name, payload, status, proposed_by, proposed_at}` where
   `payload` is already validated + allocated (cidr / port / address
   filled from the netledger). Show the diff, apply on operator
   confirmation, then the NEXT push reports it in `proposals[]` with
   `applied_by` set — that is the ONLY way a proposal leaves
   `proposed` (an entry with empty `applied_by` is refused). Polari
   never mutates the mirror on a proposal.
3. **Render** — `GET /api/vpn/render/<isle>/<network>/<peer|self>`
   returns the wg-quick text Polari thinks the isle should have
   (`PrivateKey = @@DEVICE_PRIVATE_KEY@@`, hooks are the templated
   toggles only); `GET /api/vpn/rules/<isle>/<network>/render` the
   nftables text. Treat both as a reference diff, not as config to
   copy blindly — the isle app is the authority.
4. **Config API binding** — `app.config_api` must be an isle-local
   address; the app refuses requests arriving over `wg-arch` or from
   outside the isle (handoff "Authority is the isle side").
Mock discipline unchanged: `POST /api/vpn/demo` seeds isle-a / isle-b /
isle-c with `mock_network: true`; a real push for those names replaces
the mock rows.

## 2026-09-07 — offline install gate in `isle create` (pol-core edit, for your ratification)
Context: AI-Notes/plans/OFFLINE_INSTALL_PLAN.md (2026-09-06 section) +
AI-Notes/guides/OFFLINE_BUILD_TEMPLATE.md in the suite. The platform deb now
has an OFFLINE flavor (`polari-complete-offline`, Conflicts the online one)
whose postinst writes `/etc/polari/install-mode` = `offline`. Rule: an
offline install NEVER falls back to the network — a missing part is refused
by medium section name.

The ONE edit in your tree (branch `dev-off-3` here, `isle-cli/scripts/create.sh`):
- `polari_install_mode()` helper (reads /etc/polari/install-mode, else
  $POLARI_INSTALL_MODE, else online).
- sample app: in offline mode, `docker compose up -d --no-build` when
  `isle-sample-app-sample:latest` is loaded (the medium's images/ section
  carries it), else a refusal naming the section. The `--build` path
  (pip install = internet) stays the online behaviour.
- libvirt missing + offline: continue routerless with a warning (no prompt,
  no error) — the router section is what would carry KVM bits.
Remaining off-3 (yours): the same mode check in core-install / onboard /
isle-polari-deploy (`--pull` must refuse offline) / apt-repo / store install,
via one `isle_source <section> <name>` helper — see the plan §C/§D.

## 2026-09-08 — hardware apps: the `isle vm` contract (hw-app-1, your half)
Polari now carries hardware apps as rows and renders what the isle applies:
- `GET /api/hardwareapps/render/<name>` → `{domainXml, uciScript, passthrough,
  refusals, image:{ref, sha256Raw}, requiresTier}`. `domainXml` is your
  `base-vm.xml` generalised (q35, host-passthrough, 8 pcie-root-ports, virtio
  disk at /var/lib/libvirt/images/<name>.qcow2, one virtio NIC per bridge in
  order, then usb `hostdev` / macvtap `interface type='direct'` per
  passthrough). `uciScript` is the isle-vlan-router-config idiom as one sh
  (network → dhcp → firewall zone → wireless); the PSK is read from
  `/etc/isle-mesh/<uci>.psk` on the guest (deploy-time, never rendered).
  `refusals` non-empty = do not define (e.g. image not sha-pinned).
- Store rows of kind `hardware-app` carry an install plan of
  `isle vm define <name> --from-polari` → `isle vm start <name>` →
  `isle vm status <name>`; kind `hardware-extension-app` (reticulum) →
  `isle vm status <host>` → `isle vm extend <host> --with <name>`.
- Requested verbs (extract from router-init-lib 40/50/60 + the attach libs;
  the router stays woven into the isle as is — it becomes the first caller
  with no behaviour change):
  `isle vm define <name> --from-polari` (fetch render; refuse on refusals;
  stage the image from the router image chain by `image.ref` + verify
  `sha256Raw`; write /etc/isle-mesh/vm/<name>.xml; virsh define; autostart)
  `isle vm start|stop|undefine|status <name>` (status also POSTs a
  HardwareAppState to /api/islemesh/ingest/device-style: vm_state, ip,
  uptime, probe_ok)
  `isle vm attach-usb <name> <vendor:product>` / `attach-nic <name> <iface>`
  (set DeviceLink.owner = <name>; exclusive)
  `isle vm push-uci <name>` (scp + run the uciScript over the router SSH
  key idiom)
  `isle vm extend <host> --with <name>` (reticulum: load the sidecar into
  the guest + start it; details in RETICULUM plan §5c-e / HARDWARE_APPS_PLAN §2)
  `agent.tier=hardware` via `isle onboard --host --hardware`; core qualifies.
- Guests today: `isle-relay` (relay segment VLAN 30 / 10.30.0.0/24, AP
  `isle-relay`, forwards into the isle, bearer port 4242 open) and
  `isle-guestnet` (VLAN 20 / 10.20.0.0/24, AP `isle-guest`, forward=REJECT,
  client isolation, allow-list from GuestNetworkExposure rows).

## 2026-09-09 — VPN placements: guests, router extensions, containers (vpn-4, your half)

Polari now says WHERE each of the ten isle-vpn kinds runs
(`GET /api/vpn/placements`; `VpnPlacement` rows; the store rows carry
`placement`, `guest_kind`, `vm_image_ref`, `memory_mb`, `vcpus`,
`extends`):
- **kvm** (own guest, hardware tier): `vpn-link-hub`, `vpn-link-exit`
  (OpenWrt guests, uci profiles `vpn-hub` / `vpn-exit` rendered by
  `/api/hardwareapps/<name>/render`), `vpn-bridge-server`,
  `vpn-bridge-exit` (Debian guests; the render's provisioner script
  installs openvpn + easy-rsa, builds the CA ON the guest, management on
  127.0.0.1:7505 only; exit masquerade OFF until `exit_enabled`).
  Install plan: `isle vm define <kind> --from-polari` → `isle vm start` →
  `isle vpn install <kind> --in <kind>` → `isle vm status`.
- **openwrt-extension** (on the woven router guest — we call it
  `isle-router`; tell us its real name): `vpn-link-gateway` (wireguard
  packages, wg interface in its own zone, listen port opened on the
  WAN-facing zone only) and `vpn-bridge-span` (openvpn tap bridged into
  the isle VLAN). Install plan: `isle vm extend isle-router --with <kind>`
  → `isle vpn install <kind> --on-router`. The uci script comes from the
  same render endpoint; keys are `wg genkey`'d on the router at apply.
- **container** (any member device): node, relay (blind), bridge-client,
  bridge-peer — `isle vpn install <kind>` as today.
Requested: `isle vpn install` grows `--in <guest>` / `--on-router`; the
four guests and two extensions ride the `isle vm` contract from
2026-09-08 (define/start/status/extend + HardwareAppState pushes);
`isle vpn status` pushes per-kind rows with the placement so
`/display/topology-{isle,archipelago,mesh}` show live bodies. Nothing
here changes D9: configured from the isle side only.

## 2026-09-10 — exposure loop tested over SSH from pol-core (isle url); one gate inconsistency

Ran on isle-core, remotely, as a member would from a terminal:
`isle url expose` while not an entrypoint → refused (correct) → `sudo isle url
entrypoint enable` → `sudo isle url expose polari.isle --port 18443 --user tester`
→ door live (`isle-expose-18443 0.0.0.0:18443->80/tcp`); from pol-core: 401
without credentials, 200 (PolariPlatform) with them → `sudo isle url unexpose
--port 18443` + `sudo isle url entrypoint disable` → connection refused, "the
isle is fully contained". Containment holds both ways.
One inconsistency for you: `isle security gate` answers differently by user.
As root it says "clean — deployable material only"; as the login user it FAILS,
looking at `/home/detts/polari-suite/polari-rf-node/prf-keycloak/…env` (the dev
checkout) instead of the installed material — so `isle url expose` WITHOUT sudo
is refused by the gate even though the isle is deployable. Either the gate
should resolve the installed root regardless of the caller, or `isle url
expose` should elevate (it already sudo's for the ledger). Your call.

## 2026-09-10 — full wipe + unattended core-install driven from pol-core over ssh (worked); two leftovers

`pol deploy uninstall isle-core --route isle-core --yes` (= `sudo ISLE_CONFIRM_DELETE=yes
isle uninstall --everything --force`) then `pol deploy install isle-core --route isle-core
--yes` (= scp polari-complete_0.1.33 → apt install → `sudo isle core-install
--skip-security`): router VM back, agent healthy, polari.isle 200, store 16 apps, JOIN
INFO printed (new CA D6:9F:DB:E3…). Two things for you:
1. the verify after `--everything` still lists `/usr/share/isle-mesh` present (packages
   purged, but the tree stays) — either the purge should remove it or verify should not
   count it.
2. core-install step 4 warned "no isle-app-store deb staged (~/polari-shells)" — the
   store shell is not part of what polari-complete leaves on disk after a wipe, so an
   unattended reinstall ends without the desktop door until someone builds/stages it.
Also: `isle-polari-deploy` is not on PATH after the 0.1.33 deb (teardown is) — `pol dev
deploy` breaks; core-install's own copy under scripts/ works.
The bootstrap for members is fetched the way JOIN INFO says (apt.isle resolved to the
core, sha256-checked) by `pol deploy install <node> --route isle-member`.

## 2026-09-10 — isle hardening plan (DAC + MAC), your half

`AI-Notes/plans/ISLE_HARDENING_PLAN.md` (suite): the goal is that a process owning
one app cannot cross into the OS. Survey of isle-core today: every container runs
as uid 0 inside with no userns-remap (the big gap), docker-default AppArmor +
builtin seccomp only, the agent writes host nginx config rw, ufw inactive, sVirt on
the router VM (good), host agent sandboxed (good), no security stanza in manifests.
Yours (if you ratify): sec-1 userns-remap + per-app uid + read-only rootfs + cap_drop
ALL + /etc/isle-mesh ownership; sec-2 AppArmor profiles for agent/gateway/prf-isle/
apt shipped in the deb + `isle security apply`; sec-4 DOCKER-USER rules + per-app
networks + ufw; sec-5 auditd/sysctl/unattended-upgrades; sec-6 `isle security
audit` + `escape-test` (we attack our own isle after every apply). Ours: the
manifest `security` stanza + renderer, `pol deploy audit`, SecurityControl rows,
the store card. Decisions D1–D8 are Dustin's; D7 proposes complain-mode first for
the fixed pieces so you see what the agent really touches.

## 2026-09-10 — security survey findings for the isle (from the hardening plan + os-security)

Two things the proxy/network survey found on your side:
1. `isle url expose` publishes `0.0.0.0:<port>:80` — the outside leg of a door is PLAIN
   HTTP with basic auth, so the one credential travels unencrypted. The gateway container
   should terminate TLS (the isle CA leaf for the device, or a public cert when the device
   has a name) — until then the docs say "open doors only across a VPN / trusted network".
2. `os-security/` (suite) renders per-app AppArmor profiles + seccomp + DOCKER-USER + ufw
   + sysctl + audit rules per scenario (`isle` scenario declares the agent, gateway,
   prf-isle, apt as fixed pieces). `pol deploy audit isle-core` today: verdict OPEN
   (12 pass / 13 fail): containers on docker-default, no per-app profiles, DOCKER-USER
   empty, ufw inactive, sshd on all interfaces, /etc/isle-mesh 755, no auditd. The
   escape-test under a real enforced profile on isle-core blocked all 14 attempts, so the
   profiles work; applying them at `isle app install` (security_opt + cap_drop + read_only
   from the rendered compose fragment) and running `apply.sh --scenario isle` are the
   isle-side phases (sec-1/2/4/5). The sudoers groups are at polari-cli/shells/groups/.

## 2026-09-12 — os-security: the template is an allow-list now; what the isle-side apply must know

Findings from the first sec-1a slice (suite branch dev-sec-1, ISLE_HARDENING_PLAN §13, ledger §18), all
checked on isle-core with throwaway profiles (loaded and unloaded in the same script; nothing left behind):
1. AppArmor enforces EXPLICIT `deny` rules even in complain mode, quietly. "Warn-only" therefore means an
   allow-list profile: `os-security/templates/apparmor/app.j2` is one now — the image `rmix`, the declared
   writable paths, the declared network and capabilities, the runtime's signals; complain keeps only docker's
   stock denies (no regression vs today), enforce adds Polari's. Never add a `deny` outside the enforce block.
2. The isle route keeps per-app attachment through the rendered compose fragment
   (`out/isle/compose/<app>.security.yml`: security_opt apparmor + seccomp + no-new-privileges, cap_drop ALL,
   read_only, tmpfs, pids_limit) — plain docker honours all of it. (Swarm does not: `docker stack deploy`
   drops security_opt, so the suite's server route uses a node-wide docker-default replacement instead.)
3. Always load with `apparmor_parser -r --skip-cache`: the parser cache is keyed by basename and a
   complain→enforce reload of the same file name was skipped as "same as current profile".
4. The escape test (`os-security/escape-test.sh`) now has two passes: full confinement, and `--alone`
   (profile only). The full pass blocks 14/14 with ZERO AppArmor lines — the container flags do it all; the
   profile alone in enforce blocks 11/14 (cannot stop reading a host bind — DAC/userns-remap's job — nor
   keyctl/bpf — seccomp's). The `worker` seccomp allow-list BREAKS python on musl (alpine images: "Error
   relocating python3"), so do not attach a rendered seccomp list to prf-isle-backend until the seccomp
   warn mode (SCMP_ACT_LOG, harvested from type=1326 lines) has run a day — coming as sec-1c.
5. Harvest: `python3 os-security/allowed.py --since 1d --profile isle-app-<name> --rules` = what enforcing
   would break, with the rule for each; empty list = the gate for enforce, one app at a time.
6. His rule: nothing is tested on the droplet; the home machines (pol-core, econ-core, isle-core) via app
   deployments are the test bed.

## 2026-09-12 evening — what is now LOADED on isle-core (warn-only), and one env line for polari-isle

- `pol deploy harden isle-core` ran from pol-core: the node-wide union profile (`docker-default`, rendered from the isle
  scenario's fixed pieces, keeps docker's stock denies) is loaded in COMPLAIN over every container; 65 `isle-app-*`
  profiles are loaded in complain (inert until you attach them via security_opt); seccomp lists staged in
  `/etc/polari/seccomp/` (inert; `<kind>.json` = SCMP_ACT_LOG, `<kind>.enforce.json` = ERRNO — `open` added, python +
  nginx proven under the enforce lists). Firewall/host/DAC rings NOT applied (printed). Files: `/etc/apparmor.d/isle-app-*`,
  `/etc/apparmor.d/docker-default`, `/tmp/os-security-isle/`. Revert everything MAC: `pol deploy harden isle-core --revert`
  (stock docker-default back) + `apparmor_parser -R /etc/apparmor.d/isle-app-*`. Harvest: `sudo python3
  /tmp/os-security-isle/allowed.py --since 1d --rules`.
- Please set `POLARI_DEPLOY_ROUTE=isle` in polari-isle/docker-compose.yml's backend environment: the core now tells users
  when a hardware app's hardware half cannot work on a deployment (swarm/dev → "Polari side only"); on the isle the
  route is `isle` and no notice is shown (the isle decides per device via agent tier + hwmap).

## 2026-09-13 — the purge, and the hand-back rule (his, after isle-core lost DNS)

isle-core was wiped from pol-core today: rings reverted, `isle uninstall --everything --force`, deb purged,
docker emptied, all paths removed (your own backup is under /var/backups/isle-mesh-purge-20260913-*). The
uninstall printed "Full uninstall complete" — and left the box unable to resolve names: NetworkManager still
held the router's DNS servers, but systemd-resolved had no DNS scope on the wifi link (the resolver pieces
were restarted under a live connection and the link's DNS was never re-pushed). `nmcli connection up` fixed
it. HIS RULE: "we need to ensure that our auto-install is smoothly handing back a user a fully working
default functionality Ubuntu or we may leave an everyday user stranded not knowing what to do."
The hand-back contract for the isle CLI (yours), mirrored on pol deploy / the ISO (ours):
1. an INSTALL-TIME journal of every OS-level change with its prior value (NM profiles + fields, resolved
   drop-ins / per-link DNS, /etc/hosts, sysctl, sudoers, units, apt sources, AppArmor, groups, daemon.json,
   libvirt networks, firewall) — uninstall replays it in reverse; "hand the device back" is not enough;
2. after removing resolver pieces: `nmcli device reapply <dev>` on every active connection (re-pushes DNS
   without dropping the link — a `connection up` would cut the ssh session the uninstall runs over);
3. a PROOF before the word "complete": default route present, a public name resolves, apt reaches its
   mirror, the desktop's connections autoconnect, no isle/polari residue; any failure → say what is broken
   + the one command, exit non-zero;
4. `isle rescue network` — an OFFLINE reset of resolver + profiles to Ubuntu defaults (a store door too).
Also seen: the uninstall's own verify flagged `/usr/share/isle-mesh` still present and 6 images "harmless"
— the proof should treat residue as failure. Polari side: a `handback` audit ring + a CI install→uninstall→proof
test in a throwaway VM.

## 2026-09-13 — the website deb on a wiped core: what worked, what did not (ledger §27)

`polari-complete_0.1.34_amd64.deb` from https://polari-systems.org installs cleanly and `isle core-install` builds the isle
(router VM, agent, mDNS, CA, discovery). Bugs found on the way — yours to fix in the isle CLI:
1. `isle core-install --help` IGNORES the flag and starts a real install; run as a non-root user it gets through the
   prerequisite checks and dies at `/etc/dnsmasq.d/split-dns.conf: Permission denied`. Unknown flags must print help and
   exit; non-root must refuse before touching anything.
2. `polari-isle/docker-compose.yml` hardcoded `prf-backend:staging` / `prf-frontend:staging` → `pull access denied` on
   every machine without a local build (i.e. every real user). FIXED in the suite's Isle-Mesh copy (now the only live
   copy — isle-core's ~/polari-suite was wiped): images default to `ghcr.io/dausume/prf-*:polari-v2026.09.12-core`,
   overridable by `polari-isle/.env`. The deb build should stamp the release tag into that .env.
3. The "complete" deb does not carry the isle-app-store deb → 4/7 "no isle-app-store deb staged (~/polari-shells)",
   5/7 apt-on-mesh publish FAILS → https://apt.isle and the JOIN DOOR do not exist after a website install. Members
   cannot join a core installed this way. Ship the store deb in polari-complete (or publish apt-on-mesh from the
   installed files), and make 5/7 a hard failure with the sentence, not a WARN.
4. `arp: command not found` (router.sh:87) → "Could not determine router IP address" in `isle status` and
   `isle router status`; add net-tools to Depends or use `ip neigh`.
5. `isle app list` under sudo prints the "your user is not in the docker group" warning and no list.
6. `isle dns list` prints an empty list right after polari.isle / api.polari.isle were registered (3/5 said OK).
7. `isle status`: "Broadcast domains file not found: /etc/isle-mesh/domains-to-broadcast.txt" on a fresh core.
8. 1/7 printed "[FAIL] on a NEW core: isle certs init-ca" and "leaf issuance failed for sample.local" before the CA
   existed (ordering: the CA is minted in 2/7) — reorder or silence the expected first-run failure.

## 2026-09-13 — the USB app stick (his rulings): the store's "From a USB stick" door is yours

His rulings: Polari LOOKS for a prepared USB stick, the stick is the ADVISED path for app installs, and a stick
is ALWAYS the offline flavour. Built on our side: `pol apps usb list | write <mount> [--apps all|a,b] [--from <core>]
| install [<mount>]` (polari-cli/scripts/lib/apps_usb.py + install-apps.sh). The stick layout — the contract:
  <mount>/polari-apps/index.json      {schema: "polari-app-stick/1", created, source, flavor: "offline",
                                       installers: [{file, bytes, sha256}], apps: [{module, file, bytes, sha256,
                                       verified, carries, engines, hardware (the notice text or "")}]}
  <mount>/polari-apps/*.deb           the platform installer(s) + polari-app-<module>-offline_*.deb
  <mount>/polari-apps/install-apps.sh presence-checked: skips what dpkg says is present, installs the rest OFFLINE
Yours (the isle CLI + the store shell):
1. `isle usb create <mount> --apps all|a,b` = the same stick from an isle core (its apt-on-mesh pool + the API);
   keep `isle usb create` non-destructive as it is.
2. The store's "From a USB stick" door: on open, look for a mounted removable drive carrying
   polari-apps/index.json; when found, make it the FIRST offer ("Install from your Polari stick — no internet
   needed") listing index.json's apps with their hardware notices; install through `install-apps.sh` (pkexec),
   then admit. Offline always; never fetch when a stick is present.
3. A core offered a stick: `isle apt-repo publish --from <mount>/polari-apps` so members get the apps on-mesh.
