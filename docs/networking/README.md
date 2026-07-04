# Isle-Mesh Networking

How the isle-mesh network is layered, and the distinct **profiles** it can run in.
The value of separating these is that each layer can be stood up and tested
independently — you do not need OpenWRT to exercise discovery and the agent, and the
SSH control plane is out-of-band from everything.

## The three network profiles

Isle-mesh is not one network — it is up to three layers that can exist alone or
together. Read them bottom-up: each adds capability (and attack surface) over the one
before.

| Profile | What provides addressing / resolution | Isolation | Needs OpenWRT? | Purpose |
|---|---|---|---|---|
| **1. Discovery-only** (`lightweight-local`) | mDNS/avahi + link-local / existing LAN | none (insecure) | **no** | zero-config local mesh + independent testing of discovery + agent |
| **2. SSH scaffolding** (optional, out-of-band) | mDNS `.local` **or** ISP-assigned IP | n/a (control plane) | no | cross-machine dev/control; not part of the isle data path |
| **3. OpenWRT vlan** (`core` / `remote`) | **OpenWRT DHCP + dnsmasq `.isle`** over ethernet | VLAN-isolated, ISP-invisible | **yes** | the real isle-mesh: covert, cable-based data plane |

### 1. Discovery-only — the `lightweight-local` mode

The discovery + agent layer **operates without OpenWRT**. With no router and no DHCP,
nodes find each other purely by **mDNS** (avahi + `mesh-mdns-broadcast`), navigating by
`.local` names over whatever segment already exists (wifi/LAN or a direct link). This
is mDNS-based networking in its own right — the "discovery phase with no core."

Treating it as a first-class **third install profile** (alongside `core` and `remote`)
is useful because:
- It **grounds the layering** — discovery/agent is a real, runnable network on its own.
- It **simplifies testing** — you can validate discovery, the agent proxy, and app
  registration without standing up the OpenWRT VM.
- It is a legitimate lightweight deployment where VLAN isolation isn't required.

⚠️ **It is explicitly insecure / unisolated** — no VLAN, traffic rides the existing
(likely ISP) LAN, mDNS is broadcast in the clear. Not for the covert threat model;
for testing and casual local use.

### 2. SSH scaffolding — out-of-band control (may not apply)

The cross-machine dev/control channel (the "3-box SSH bridge"). It is **separate from
the isle data path** and does not always apply (a deployed isle needs no SSH bridge).
Two variants — see `interface-profiles.md`:
- **`.local`-only / no-internet** — mDNS names over a local segment, no ISP.
- **ISP-wifi-device based** — over the normal ISP LAN, by pinned IP (preferred for
  reliability) or `.local`.

### 3. OpenWRT vlan — the primary layer (`core`/`remote`)

The real isle-mesh. **OpenWRT DHCP + dnsmasq (`.isle`) over the dedicated ethernet
cable** is the primary addressing + resolution layer; nodes get routable leases and
resolve each other via `.isle`. VLAN-isolated and invisible to the ISP. The UDP-7878
join beacon lives here. In this profile **avahi/mDNS steps back to a
discovery/onboarding role only** and must NOT answer for isle-node addresses (that is
dnsmasq's job) — see failure-modes.

## Protocol legend

| Protocol | Provided by | Role |
|---|---|---|
| **mDNS / `.local`** | avahi-daemon + `mesh-mdns-broadcast.sh` (`avahi-publish`) | zero-config **discovery** + `.local` app names |
| **DHCP** | OpenWRT (dnsmasq) | **primary** isle addressing (leases over the cable) |
| **dnsmasq / `.isle`** | OpenWRT | **primary** isle name resolution |
| **Discovery beacon** | OpenWRT init.d `isle-discovery` → UDP `255.255.255.255:7878` | announces the isle so a remote can `isle join` |
| **SSH** | host sshd | out-of-band control/dev (scaffolding profile) |

## How the layers relate

- Discovery + agent (profile 1) run **standalone**; OpenWRT (profile 3) is added on top
  to make it a real isolated vlan.
- Once profile 3 is up, **DHCP/dnsmasq own isle addressing** and mDNS narrows to
  discovery-only — it does not become useless, it stops competing (see failure-modes).
- SSH scaffolding (profile 2) is **orthogonal** to both — a control channel, never the
  isle data path.

## Index

- [`interface-profiles.md`](interface-profiles.md) — per-scenario, per-protocol
  interface tables (what runs over which interface in each profile).
- [`failure-modes.md`](failure-modes.md) — "if this broadcast reaches this interface,
  X breaks because Y", and the guard for each.

_Reference data captured from the live testbed (isle-core / lightweight) 2026-07-04._
