# Isle-Mesh Revamp & Isle-App Lifecycle — Master Plan

North star: **tear down → bring back up in one go, with real apps.** The platform lets
you self-host complex docker-compose apps and network them together covertly. Arbitrary
compose apps → wrapped **isle-apps** → portable **.debs** → installed on any node →
brought up/down/de-registered/uninstalled from the **management app** (double-click an
app icon).

This plan is executed as **independent branches off `revamp-isle-2machine`**, each a
self-contained, individually testable unit with a documented test procedure, so they can
be tested one by one and merged selectively.

---

## Where we are (verified this session)

**Working & proven:**
- Covert 2-machine isle over a dedicated cable (~1ms bidirectional); remote leases from
  OpenWRT (never hijacking the ISP route).
- Plug-and-play: cable self-configures OpenWRT (`add-connection`), remote auto-lease,
  discovery-on-install, cable hotplug, boot self-recovery.
- Router DNS on the isle (was crash-looped by a `configure join` bug — fixed live),
  `.isle` authoritative, cross-node resolution, and `isle dns register` (deduped).
- CLI foundation coherent (table-driven dispatcher; router module cleaned).

**Built but disjointed (the connective tissue):**
- `isle app` lifecycle CLI (init/scaffold/up/down/logs/ps/prune), compose→isle-app
  scaffolding, manager-app AppsView — all exist.
- BUT: the registry is wiped on agent restart; register→nginx serving doesn't persist;
  `.isle` app-scoping isn't wired to app-up; no reconcile-on-bringup; remote lacks
  split-DNS; `configure join` crash-loops dnsmasq; per-app `.deb` packaging unclear.

**Format finding (important):** `agent register`'s schema and `generate-nginx-configs.sh`
already MATCH (`.apps[name].services[]`, with automatic `.local`→`.isle`). The break is
persistence/ownership, not schema.

---

## The core idea: registry = durable desired-state

The registry (`/etc/isle-mesh/agent/registry.json`, bind-mounted read-only into the
nginx agent) is the platform's **durable memory of which isle-apps are installed + their
run-state + `.local`/`.isle` scoping**. Fix its ownership:
- **Merge-only, never wipe** — drop the "Clearing stale app registry" on agent start;
  init only adds `health` if missing; `register`/`unregister` are the only app mutators.
- **Bring-up = reconcile** — `isle agent start` reads the durable registry and, per app,
  ensures containers up + regenerates nginx (`.local`+`.isle`).
- **Manager app drives the state** — AppsView reads the registry; double-click →
  access / up / down+deregister / uninstall.

---

## Branch roadmap (each = one branch off `revamp-isle-2machine`)

Ordered by dependency & value. Each branch note lists: goal · files · test.

### B1 — `fix/registry-durable`  (highest value; unblocks everything app-serving)
- **Goal:** stop wiping registered apps on restart; registry becomes durable.
- **Files:** `isle-cli/scripts/create.sh` (remove the `echo '{...,"apps":{}}' > REGISTRY`
  clear in `setup_agent`), `isle-agent/scripts/agent-manager.sh` (init: add-health-if-
  missing only, never reset `.apps`).
- **Test:** `sudo isle agent register --name sample --domain sample.local --container
  isle-sample-app --port 5000 --protocol http`; `sudo isle agent restart`; confirm
  `sample` still in `/etc/isle-mesh/agent/registry.json` and nginx serves `sample.local`
  + `sample.isle` (curl -H Host:sample.isle to the agent).

### B2 — `fix/dns-join-crashloop`  (retire the DNS bug + use the register recipe)
- **Goal:** `isle router configure join` must NOT crash-loop dnsmasq; wire it to the
  proven `.isle` recipe (local=/isle/ + UCI domain), retire the mDNS-discovery daemon
  that discovers nothing.
- **Files:** `openwrt-router/scripts/router-setup/configure-join-protocol.sh` (remove the
  `conf-dir=/etc/dnsmasq.d` edit to /etc/dnsmasq.conf — the crash cause; ensure
  local=/isle/ once). Optionally make it register the known nodes/apps via the
  `isle dns register` mechanism instead of the mDNS scan.
- **Test:** on a router with the bad conf-dir, run configure join; verify dnsmasq stays
  up (netstat 10.10.0.1:53) and `.isle` still resolves.

### B3 — `feat/remote-split-dns`  (natural `curl app.isle` on remotes)
- **Goal:** the remote forwards `.isle` → the isle router (10.10.0.1), like the core
  host's `ensure_isle_dns`, without touching its ISP DNS.
- **Files:** new `isle-cli/scripts/remote-dns.sh` (+ wire into `remote-lease` or a new
  `isle dns use-router` verb): add a systemd-resolved drop-in / NM `ipv4.dns` for the
  isle interface routing `~isle` to 10.10.0.1.
- **Test:** on the remote, `curl http://sample.isle` resolves+reaches (not just explicit
  `nslookup … 10.10.0.1`).

### B4 — `feat/reconcile-on-bringup`  (the "one-go" bring-up)
- **Goal:** `isle agent start` (and boot-recovery) reconcile the durable registry: for
  each installed app, ensure its containers + nginx block; publish its `.isle` name via
  `isle dns register`.
- **Files:** `isle-agent/scripts/agent-manager.sh` (add a reconcile step), hook from
  `boot-bringup.sh`. Depends on B1.
- **Test:** register 2 apps; `isle recover`; both come up + resolve `.isle` on both nodes.

### B5 — `feat/app-isle-scope`  (per-app `.local` vs `.isle` scoping)
- **Goal:** `isle app up`/register writes the app's scope (modes: local|isle) so
  generate-nginx-configs emits the right server_name(s) and `isle dns register` runs for
  `.isle`-scoped apps.
- **Files:** `agent-manager.sh` register (accept `--scope`), `generate-nginx-configs.sh`
  (honor modes), `isle app` up flow.
- **Test:** an `.isle`-scoped app is reachable cross-node; a `.local`-only app is not on
  the isle.

### B6 — `feat/isle-app-deb`  (portable packaging) + manager AppsView lifecycle
- **Goal:** wrap an isle-app into a `.deb` installable on another node; AppsView
  double-click → access/up/down+deregister/uninstall driving the CLI verbs.
- **Status (2026-07-04): packager BUILT + build-tested headlessly.**
  - `isle app package --name <n> --compose <file> [--domain --container --port
    --protocol --version --output --icon --maintainer]` -> `isle-app-<n>_<v>_all.deb`
    (`isle-cli/scripts/app-package.sh`). Verified: valid control metadata
    (`Depends: docker.io | docker-ce`), correct file layout, and the per-app lifecycle
    wrapper with its `__APPROOT__` placeholder substituted.
  - The `.deb` ships: payload `docker-compose.yml` + `isle-app.env` under
    `/usr/share/isle-mesh/apps/<n>/`; a lifecycle wrapper `/usr/bin/isle-app-<n>`
    (`up`=compose up + `isle agent register`; `down`=`isle agent unregister` + compose
    down; `status`; `access`=xdg-open the domain); a `.desktop` entry (double-click ->
    `up`); `postinst` (records an installed-but-down marker under
    `/etc/isle-mesh/agent/installed-apps/`, does NOT auto-start); `prerm` (down +
    deregister + forget).
  - `isle app installed [--json]` (`app-installed.sh`) lists installed-but-down apps by
    reading those markers — the CLI verb AppsView needs to show installed apps and offer
    "bring up". Tested (empty + populated).
- **Test (needs sudo on a node — DO WHEN BACK):**
  1. `isle app package --name demo-wiki --compose <compose> --port 80` -> produces the `.deb`.
  2. Copy to the remote node, `sudo dpkg -i isle-app-demo-wiki_0.1.0_all.deb`
     (postinst prints the bring-up hint; `isle app installed` now lists it, down).
  3. `isle-app-demo-wiki up` (or from the manager app) -> compose up + registered; served
     on `demo-wiki.local` / `demo-wiki.isle` via the proxy. `... access` opens it.
  4. `isle-app-demo-wiki down` -> deregistered + containers down. `sudo dpkg -r
     isle-app-demo-wiki` -> prerm brings it down + deregisters + drops the marker.
- **AppsView lifecycle wired (2026-07-04, submodule `feat/appsview-deregister`):** the
  view merges `isle agent list-apps` (running) with `isle app installed --json`
  (deb-installed, carrying each app's `pkg`) and renders state-aware cards —
  running: Open / Bring Down / De-register; installed-down: Bring Up / Uninstall
  (`pkexec dpkg -r`). Slow actions run off the FX thread. Compiles clean (mvn -o,
  BUILD SUCCESS). Main repo pins this submodule commit.

### Bx — cleanup (needs sudo, do when back)
- Remove router test cruft: `test.isle`/`test2`/`test3` UCI domains + the manual
  `address=` line in `/etc/dnsmasq.conf`. (`isle dns unregister test*` + edit.)

---

## Suggested order
B1 → B2 → B3 (independent, low-risk, high-value) → B4 (needs B1) → B5 → B6.
B1/B2/B3 touch disjoint files and can be tested/merged in any order.

_Recipe reference: `.isle` = `local=/isle/` in /etc/dnsmasq.conf + one deduped UCI
`domain` entry per name (uci add dhcp domain; name+ip) + dnsmasq reload. NOT a conf-dir
in the procd jail (that crash-loops dnsmasq)._
