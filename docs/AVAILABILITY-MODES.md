# Isle-Mesh Availability Modes

**Why:** the threat model demands (a) *survive reboots unattended* — no interactive
login needed — and (b) *minimize emissions/footprint* — don't run apps nobody is using.
Those two pull in opposite directions, so availability becomes an explicit per-unit
property: **always-available** (up whenever the host is up) vs **on-demand** (up only
while someone is actively using it). Applies to the **router** and to each **isle-app**.

`availability_mode: always-available | on-demand`  — default **always-available**.
Stored on each app's registry entry + installed-app marker (and on the router config).
The management app reads/sets it; the CLI enforces it.

---

## Login-dependency findings (2026-07-05, isle-core)

Core services are already system-level and survive reboot without login:
- docker.service **enabled**; `isle-vlan-agent` + `isle-sample-app` are `restart=unless-stopped`.
- `isle-mesh-boot.service` is `WantedBy=multi-user.target` (runs at boot, no `User=`).
- no `systemctl --user` isle services exist.

Real gaps for TRUE always-available:
1. **Router VM autostart is OFF** — it comes up only via `boot-bringup` (timing-dependent).
   Fix: `virsh autostart <vm>` so libvirtd starts it independently.
2. **User linger is OFF** (`Linger=no`) — harmless today, but any future user-scoped unit
   would need login. Fix: `loginctl enable-linger <user>` (defensive).
3. The "had to login" symptom was really the **cert crash-loop** (0-byte SSL cert →
   vlan-agent restarting) — fixed separately (see fix-live-certs.sh).

---

## always-available (IMPLEMENT NOW — the default)

Guarantee: whenever the host is powered on, the router + all always-available apps are up,
with no login. Concretely:
- **Router:** `virsh autostart <vm>` at create/router-up; `boot-bringup` still reconciles.
- **Agent + apps:** containers `restart: unless-stopped`; `boot-bringup` reconciles from the
  (now durable) registry and brings every always-available app UP.
- **Host:** `loginctl enable-linger <user>` during install.
- **Bake into `create.sh`** so a fresh install is always-available by default; no one-off scripts.
- Management app: shows each unit's mode; "always-available" is enforced by the reconcile
  on boot + a manual "ensure up" action.

## on-demand (SCAFFOLD + PLAN — later)

An on-demand app is *installed but DOWN* by default; it wakes when someone actually uses it,
then idles back down. Flow:

1. User on **device A** opens `<app>.isle` (the app is installed on **device B**, down).
2. Device A's proxy sees no healthy backend for an on-demand app → instead of failing,
   it emits a **wake request** for that app.
3. The **wake request travels the mesh control plane** to the device that hosts the app.
   (Reuse the existing host-agent / **device-relay** channel — it already relays between
   devices — with a new `wake-app <name>` message. "Who hosts the app" comes from the
   installed-app markers / mesh registry.)
4. **Device B brings the app UP** (`isle-app-<pkg> up`) and re-registers it.
5. Device A's request **proceeds** — proxy retries with backoff, or shows a brief
   "starting…" holding page until the backend is healthy, then routes through `.isle`.
6. An **activity tracker** records "user active on <app>"; after an **idle timeout**
   (per-app knob) with no activity, the host **brings the app back DOWN**.

Scaffolding/capabilities needed:
- Shared `availability_mode` field (registry entry + marker) — also unblocks always-available.
- **Wake control-plane message** on device-relay (`wake-app`, `app-active`, `app-idle`).
- **Host directory of app→device** (which device hosts which installed app) — extend the
  mesh registry / device-relay knowledge.
- **Proxy behavior for down on-demand apps:** detect on-demand + down → trigger wake +
  hold/retry (a "waking up" holding page) instead of 502.
- **Activity + idle tracker** per on-demand app + idle-shutdown timer (per-app timeout knob).
- **Management app UI:** per-app mode toggle; for on-demand apps, show "active users" and
  last-woken; manual wake/sleep.
- **Notification:** "a user is now using <app>" surfaced to the hosting device (the
  "hey, someone is using this" signal Dustin described).

Open questions:
- Wake latency UX (holding page vs connection hold) and per-app idle-timeout defaults.
- Auth/allowlist for who may wake an app (don't let anyone spin up a hosting device's app).
- Cross-device routing once up = the existing `.isle` model (device-resolve → nginx hop).

---

## Build order
1. `availability_mode` field (registry + marker) + default always-available. (shared foundation)
2. always-available enforcement: `virsh autostart` + `loginctl enable-linger` in create.sh;
   boot-bringup reconciles always-available apps up.
3. Management app: show/set mode.
4. on-demand scaffolding: device-relay wake message → proxy wake-hold → activity/idle tracker.

---

## Extended availability modes

Rather than a fixed enum, availability is really a composition of **up-trigger ×
down-trigger × placement**. Named modes are presets over that model (intuitive names +
composable knobs underneath, per the isle knobs-and-suggestions rule):

- **up-trigger:** boot | access | schedule | presence/quorum | manual | resource-permitting
- **down-trigger:** never | idle-timeout | schedule-end | presence-lost | manual | resource-pressure
- **placement:** single-host | replicated (failover across devices)

Named presets:
1. **always-available** — up:boot, down:never, single. *(default)*
2. **on-demand** — up:access, down:idle-timeout, single.
3. **scheduled** — up during declared windows (e.g. a coordination app only 07:00–23:00,
   or only for the duration of a declared session), down at window end. Predictable access
   + emissions control.
4. **presence-gated / quorum** — up only when ≥N members (or a specific device) are on the
   isle; down when presence drops below threshold. Fits democratic coordination (don't run
   the shared app unless enough people are present) and covert operation (no idle service
   when nobody's around).
5. **replicated / failover (HA)** — installed on several devices; one primary, others
   standby; if the primary drops off the isle, a standby is promoted (brought up +
   re-registered). "Always-available across devices" — resilience against a device being
   seized, lost, or powered down. Important for covert infra.
6. **manual / pinned** — never auto-managed; only explicit operator up/down. For sensitive
   apps that must not auto-start or be woken by others.
7. **resource-aware (modifier)** — a gate layered on any start decision: suppress start when
   the host is on battery / thermally throttled / over a data budget. Ties to the low-power
   N95 mesh nodes and [[resource-aware-simulation]]-style budgeting; availability conditioned
   on a resource budget.

Related but distinct — **duress / dead-man** (a *trigger*, not a steady mode): brings
everything down (or wipes) on a panic signal or a missed check-in. Lives in the
kill-switch/threat-model layer but shares the control plane with on-demand wake.

**Why on-demand is the keystone:** modes 3–5 reuse the same control-plane primitives
on-demand needs (wake message on device-relay, activity/presence tracker, app→device
directory). Build on-demand first; scheduled/presence/replicated are then mostly policy on
top. `manual` is trivial (no automation); `resource-aware` is a gate on any start decision.
