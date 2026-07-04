# Networking Failure Modes

Format: **if `<protocol/broadcast>` reaches `<interface>`, `<what breaks>` because
`<reason>`** → **guard**. Ordered roughly by how often it bites.

These are the "a broadcast reaching the wrong interface breaks things" cases. They are
kept separate from the interface profiles on purpose — the profiles say what *should*
run where; this says what goes wrong when something runs where it shouldn't.

---

### F1. avahi advertises the host name on the isle / internal interfaces
**Trigger:** avahi-daemon is unscoped (no `deny-interfaces`), so
`<host>.local` is answered on **every** interface — including `enp1s0` (isle cable,
IPv6 link-local), `isle-br-0`, `br-mgmt` (192.168.1.254), docker (172.20.0.1), `vnet*`.
**Breaks:** a resolver on a *different* segment (e.g. the controller on wifi) receives
an address it cannot route to (an isle link-local, or a bridge IP) → **intermittent
connection failures** as resolution rolls between reachable and unreachable addresses.
**Reason:** mDNS returns the union of all interface addresses; some are only valid on
their own link. This is NOT a timing race — the bad addresses are simply unroutable.
**Guard:** scope avahi to the discovery interface(s) only —
`deny-interfaces=enp1s0,isle-br-0,br-my-isle,br-mgmt,virbr0,vnet0,vnet1,docker0,<agent-net>`
(or an explicit `allow-interfaces=`). In the OpenWRT profile the isle plane is owned by
DHCP/dnsmasq, so avahi has no business answering there.

### F2. Isle-node address resolved via avahi instead of dnsmasq `.isle`
**Trigger:** relying on `.local`/avahi to reach an isle node once OpenWRT is up.
**Breaks:** you may get the pre-DHCP link-local or a stale address instead of the
routable DHCP lease → wrong/unreachable target.
**Reason:** DHCP + dnsmasq `.isle` is the **primary** layer once the cable is up; avahi
is only discovery/scaffolding and is not authoritative for isle addresses.
**Guard:** isle automation resolves isle nodes via `.isle` (dnsmasq); use avahi only
for pre-lease discovery/onboarding.

### F3. Duplicate / cycling mDNS broadcasters
**Trigger:** more than one `mesh-mdns-broadcast.sh` running (observed: 2 long-lived
procs), and/or duplicate entries in the domain list (observed: `health.local` twice).
**Breaks:** repeated name-conflict / network-error notifications, wasted publishes,
avahi renaming (`name-2.local`).
**Reason:** two publishers claim the same name; avahi treats it as a conflict.
**Guard:** single broadcaster instance; de-duplicate the domain list; make the
systemd unit stop the old instance before starting a new one.

### F4. mDNS broadcasting on the ISP wifi in the covert profile
**Trigger:** avahi/`mesh-mdns` active on `wlp2s0` (ISP LAN) while running the covert
OpenWRT profile.
**Breaks:** the isle's existence/names are announced on the ISP-visible segment →
**detectability** (violates the threat model).
**Reason:** mDNS is broadcast in the clear to the whole segment.
**Guard:** gate mDNS to `isle discovery` sessions (quiet in steady state); keep
discovery off the ISP interface once nodes are onboarded.

### F5. `.isle` queried before OpenWRT/dnsmasq is up
**Trigger:** resolving a `.isle` name when the router VM isn't running / DHCP not
serving.
**Breaks:** NXDOMAIN / hang.
**Reason:** the authoritative resolver (router dnsmasq) isn't answering yet.
**Guard:** bring the router up first (boot-recovery does this); have automation fall
back to discovery or wait-for-router.

### F6. Multiple addresses on the control interface (e.g. two DHCP leases)
**Trigger:** the host holds two IPv4 leases on wifi (observed: .24 and .25).
**Breaks:** resolver ambiguity; automation keyed on "the IP" may pick the wrong one.
**Reason:** the name/host has more than one A record on the same segment.
**Guard:** pin control-plane access to one stable IP; investigate why two leases exist
(release the extra, or reserve one).

### F7. Reserved isle cable carries a host IP
**Trigger:** the physical isle port (`enp1s0`) keeps an IP address.
**Breaks:** a host-level route/leak onto the isle segment; reduces isolation/stealth.
**Reason:** an addressed host interface participates in L3 on the isle segment.
**Guard:** `add-connection` flushes the IP and enslaves the port into the isle bridge;
boot-recovery re-flushes on every boot.

### F8. macvlan orphaned after reboot (agent can't attach)
**Trigger:** the isle bridge is recreated with a new ifindex on reboot, orphaning the
docker macvlan built on it.
**Breaks:** `isle-vlan-agent` fails to start ("network … not found").
**Reason:** the macvlan's parent link identity changed underneath it.
**Guard:** boot-recovery rm+recreates the macvlan and force-recreates the agent
(`isle recover` / `isle-mesh-boot.service`).

---

_Add new modes here as they are found. If a failure is really a design constraint
(not a bug), note it in interface-profiles.md instead of here._
