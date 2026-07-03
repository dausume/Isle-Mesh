# Mesh Convergence Plan — Isle-Mesh × Polari

_2026-07-03, from Dustin's brief. This document is deliberately shared ground between the
two projects (and the two Claude instances): committed in polari-suite AND copied to
isle-core:~/Isle-Mesh/. Each project keeps its own memory; this plan is the seam._

## Purpose (Dustin's framing)
Isle-Mesh generalizes making a capable, generalizable VLAN on top of the normal LAN, and
its CLI generalizes making apps able to talk to one another through automation
("mesh-apps"). Polari rides it as the first first-class mesh-app, with its node network
(parent/child) auto-configuring from mesh presence. Weave the two coherently, with
minimal footprint per role.

## 1. Component matrix (coherent + minimal footprint per variant)

| Component  | HOST                                            | REMOTE |
|------------|--------------------------------------------------|--------|
| CLI        | full: create/destroy/scan/appify/package/…       | thin subset: join/leave/integrate/status |
| Manager app| full UI                                          | SAME package, COLLAPSES to client mode on detection (no router capability → client) |
| Agent      | optional (host's own apps register locally)      | required: DHCP/macvlan attach + app-registration relay |
| Router VM  | yes (OpenWRT qcow2, DHCP + .isle DNS)            | never |

Acceptance rule: nothing installs on a remote that only the host needs, and vice versa —
audit each variant's payload against this table.

## 2. Mesh-app pipeline (isle CLI)
1. `isle appify <docker-compose.yml>` — scaffold an existing compose for the mesh:
   network attachment (macvlan on isle-br-0, DHCP from the router), .isle DNS names
   (the existing auto-.isle nginx generation), lifecycle hooks, mesh labels.
2. `isle package <scaffolded-app>` — generate a **Debian package** from the compose:
   payload = compose + scaffolding + assets; `postinst` = SELF-INTEGRATION:
   - detect isle-mesh presence (thin CLI installed? agent running? isle joined?)
   - present → `isle app integrate` (attach networks, register DNS, announce)
   - absent → run as a plain local compose app (**graceful degradation** — a mesh-app
     is still just an app; the mesh is an upgrade, not a dependency).
3. Average-user story (the acceptance narrative): install manager app on the main
   machine → install the same manager app on other machines (client-collapse) → install
   any isle-generated .deb on any device; apps weave themselves in.

## 3. Polari as the first first-class mesh-app (the weave)
- Polari's .deb is produced by the SAME pipeline from its compose (the twin build's
  INSTANCE_ID parametrization is the direct prep).
- First-boot mesh logic in the Polari backend (all knobs-and-suggestions compliant —
  detection SUGGESTS, explicit knobs decide, auto is the default posture):
  1. **Detect** mesh via the "mesh facts" interface (below).
  2. **Register** itself: claim `polari-<host>.isle`.
  3. **Discover** existing Polari instances on the mesh (registry/DNS lookup of the
     well-known parent claim + peer ping on discovered names).
  4. **Conditional role**: none found → configure as PARENT (claim the parent name);
     parent exists → configure as CHILD: register as the parent's peer (token via mesh
     trust, see open point), pull the coordination module via the modules API
     (/api/modules install from peer), assume Track 4 member role.
- Division of responsibility (the coherence rule): **Isle-Mesh owns** L2/L3 (VLAN, DHCP,
  .isle DNS), packaging, app scaffolding/integration, device inventory. **Polari owns**
  application peering, modules, placement, simulation coordination. Isle-Mesh never
  learns what Polari is (it's just a mesh-app) — information hiding between projects.

## 4. The seam: the "mesh facts" interface
A small READ interface the agent/thin CLI exposes and any mesh-app may consume:
  am-i-meshed? · my .isle name · known devices · existing service-name claims ·
  claim(name) / release(name) with atomic first-claim-wins semantics.
Shape TBD by the Isle-Mesh side (CLI subcommand with JSON output is fine: `isle facts
--json`); Polari consumes it read-only + performs claims. Keep it one-way and minimal.

## 5. Open design points (Dustin to rule)
1. **Parent-claim race**: two Polaris installed near-simultaneously. Proposal:
   first-claim-wins via the atomic mesh-registry claim; the loser configures as child;
   a manual role knob overrides either way; the manager app surfaces the topology.
2. **Child token distribution — RULED (Dustin, 2026-07-03): mesh-carried, but with an
   explicit AGREEMENT.** Admission flow: the child sends a JOIN REQUEST over the mesh
   channel (identity: hostname, .isle name, instance fingerprint, requested role) — never
   auto-admitted. The request becomes a durable PeerAgreement object on the parent
   (status pending), surfaced in the manager surfaces (suggestion: "device X asks to join
   as child"; knob: approve/deny). On approval the parent mints a PER-CHILD, scoped,
   REVOCABLE token delivered over the mesh channel; the child confirms which parent it is
   joining (bilateral consent — matters once multiple isles exist); the agreement records
   who approved, when, and scope, on both sides. Revocation = deleting the agreement (no
   shared secret to rotate). Optional convenience knob, DEFAULT OFF: auto-approve devices
   already on my isle. Later: Keycloak federation replaces the token mechanics; the
   agreement object remains the consent record.
3. Degradation depth — **RULED (Dustin, 2026-07-03): yes.** An unmeshed .deb functions
   as a standalone normal docker-compose app, reachable via its `.local` name (mDNS —
   which also resolves from other devices on the same plain LAN, so it's discoverable
   with zero configuration). This is additive by design: appify's scaffolding already
   generates `.local` server names as the base and `.isle` as the mesh layer — joining a
   mesh later ADDS `.isle` without touching `.local`. Postinst nuance for the unmeshed
   path: verify avahi presence / ride `<hostname>.local`; zero mesh components installed.

## 6. Sequencing across the two projects
- Isle-Mesh side (other instance): appify/package (.deb gen) hardening; the component-
  matrix footprint audit; the `isle facts` read interface + atomic claims.
- Polari side (this instance): finish the no-code foundations workstream (in flight);
  then mesh-detection + role auto-config module (consumes `isle facts`), .deb-friendly
  first-boot behavior, PeerNode base_url on .isle names; Track 4 placement rides it.
- Integration milestone: **two machines, manager app + one .deb each, zero manual
  network/peer configuration → a parent/child Polari pair exchanging a module.**

## 7. Polari-side Phase 1 (concrete tasks — START HERE post-session-clear)
Buildable NOW, against the existing twin, without waiting on isle-mesh deliverables:
1. **PeerAgreement admission flow** (replaces the twin's shared token, per ruling §5.2):
   PeerAgreement definition class (requester identity/fingerprint, requested role, status
   pending|approved|denied|revoked, scope, approvedBy/At, token_hash per-child);
   endpoints: POST /api/peers/join-request (child→parent), GET /api/peers/agreements,
   POST /api/peers/agreements/{id}/approve|deny (mints per-child scoped token, returns
   over the channel)|revoke; child-side: request → poll/receive → store token → register.
   Manager surface later; API + selftests + live twin verify first (B joins A via
   agreement instead of the shared env token; keep the env token as a deprecated fallback
   knob during transition).
2. **Mesh-detection module** (polariPeers/mesh_facts.py): consume `isle facts --json`
   when the CLI exists on the host; graceful absence (returns meshed:false); MOCKABLE
   (env POLARI_MESH_FACTS_CMD override) so role auto-config develops before isle-mesh
   ships the real subcommand.
3. **Role auto-config** (first-boot logic): detect → register .isle name (claim via mesh
   facts claim() when available; else skip) → discover existing Polari (mesh facts /
   direct probe list) → none: parent; found: child → SEND JOIN REQUEST (flow #1) →
   on approval pull coordination module (existing modules API). All knobs-and-suggestions:
   role knob (auto|parent|child) with auto default; every auto decision logged with
   evidence.
4. **Small enablers**: PeerNode base_url accepts .isle hostnames (trivial); first-boot
   idempotency (re-running auto-config is safe); the deprecated-shared-token fallback knob.
Isle-Mesh side (other instance, unchanged from §6): appify/package hardening, footprint
audit, `isle facts` + atomic claims. Integration test when both sides land: the §6
milestone.
