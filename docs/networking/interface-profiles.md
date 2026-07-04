# Interface Profiles — per scenario, per protocol

For each network profile, this lists the interfaces involved and which protocol
operates over each. Reference addresses are from the isle-core testbed; treat the
**role** column as the durable part (addresses vary per host/DHCP).

Interface roles seen on the testbed (isle-core):

| Interface | Role / plane |
|---|---|
| `wlp2s0` | ISP wifi (the LAN the ISP router provides) |
| `enp1s0` | **isle cable** (dedicated ethernet, reserved for the isle vlan) |
| `isle-br-0` | isle bridge — router isle-NIC + agent macvlan parent |
| `br-mgmt` | host↔router management bridge (192.168.1.0/24) |
| `br-<isle>` (`br-my-isle`) | bridge a reserved physical cable is enslaved into |
| `br-8035…` | docker `isle-agent-net` (app↔agent) |
| `docker0` / `virbr0` | docker / libvirt defaults (unused by isle) |
| `vnet0/1` | router-VM tap interfaces (→ br-mgmt / isle-br-0) |

Legend: ✅ = intended here · ➖ = present but not used · ❌ = must NOT run here
(see failure-modes).

---

## Profile 1 — Discovery-only (`lightweight-local`)

No OpenWRT, no DHCP. Nodes navigate by mDNS `.local` over the existing segment.

| Interface | Address source | mDNS/`.local` | DHCP | `.isle` | 7878 beacon | SSH |
|---|---|---|---|---|---|---|
| `wlp2s0` (wifi/LAN) | ISP DHCP or static | ✅ discovery + `.local` app names | ➖ (ISP's) | ❌ n/a | ❌ n/a | ✅ (if scaffolding) |
| `docker0` / agent net | docker | ➖ | ➖ | ❌ | ❌ | ❌ |

- **Discovery** = avahi + `mesh-mdns-broadcast` publishing `.local` names.
- **Agent** = nginx proxy serving registered `.local` apps.
- No isolation; this is the "mDNS-only network." Good for testing discovery + agent
  without the router.

---

## Profile 2 — SSH scaffolding (out-of-band control)

Two variants. This plane is for humans/automation to reach nodes, **never** the isle
data path.

### 2a. `.local`-only / no-internet
| Interface | Address source | mDNS/`.local` | SSH target |
|---|---|---|---|
| local segment NIC (wifi or direct link) | link-local / static / mDNS | ✅ name resolution | `.local` name over the segment |

- Works with **no internet at all**. Relies on mDNS to resolve peer names.
- Fragile if the name resolves to multiple addresses (see failure-modes) — prefer a
  connect-tested address.

### 2b. ISP-wifi-device based
| Interface | Address source | resolution | SSH target |
|---|---|---|---|
| `wlp2s0` (ISP wifi) | ISP DHCP | pinned **IP** (preferred) or `.local` | IP over the ISP LAN |

- **Human/dev access defaults to the pinned IP** (reliable; `.local` is intermittent).
- **isle automation** (incl. remote-install) defaults to trying `.local`/isle-native
  first and **asks before falling back to the ISP-LAN IP** (threat-model: don't
  silently route isle automation over the ISP-visible LAN).
- Note: traffic here transits the ISP wifi router — visible to it as metadata.

---

## Profile 3 — OpenWRT vlan (`core` / `remote`) — the primary layer

Router up, cable connected, DHCP serving. **DHCP + dnsmasq own isle addressing +
resolution.** avahi narrows to discovery-only and must not answer isle-node addresses.

| Interface | Address source | mDNS/`.local` | DHCP (OpenWRT) | dnsmasq `.isle` | 7878 beacon | SSH |
|---|---|---|---|---|---|---|
| `enp1s0` (isle cable) | **OpenWRT DHCP lease** | ❌ (scope avahi OFF) | ✅ leases | ✅ resolves | ✅ broadcasts | ❌ |
| `isle-br-0` | (bridge, router NIC) | ❌ | ✅ (bridged) | ✅ | ✅ | ❌ |
| `br-<isle>` | (bridge for reserved cable) | ❌ | ✅ | ✅ | ✅ | ❌ |
| `br-mgmt` (192.168.1.254) | static | ➖ | ➖ | forwards `.isle`→router | ➖ | host↔router only |
| `wlp2s0` (ISP wifi) | ISP DHCP | ✅ **discovery/onboarding only** | ❌ | ❌ | ❌ | ✅ (scaffolding) |
| `br-8035…` (agent net) | docker | ➖ | ➖ | ➖ | ❌ | ❌ |

- **Primary path:** a node on the cable gets a DHCP lease from OpenWRT and resolves
  peers via `.isle` (dnsmasq authoritative). The host forwards `.isle` queries to the
  router at 192.168.1.1 (`ensure_isle_dns` in `create.sh`).
- **avahi's job here is discovery/onboarding only** (ideally gated to
  `isle discovery` sessions) — it must be **scoped off** `enp1s0`/`isle-br-0`/`br-*`/
  `vnet*` so it never answers isle-node addresses. That is the fix for the intermittent
  resolution documented in failure-modes.
- **Isolation + stealth:** the isle vlan rides the dedicated cable, invisible to the
  ISP; the reserved cable's host IP is flushed (no host address on the isle port).
