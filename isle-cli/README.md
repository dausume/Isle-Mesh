# Isle-CLI

A CLI tool for managing Isle-Mesh: zero-configuration mesh networking for containerized applications.

## Installation

Use 'npm link' in the isle-cli folder to register the isle cli to your computer's npm:

```bash
cd isle-cli
npm link
```

After doing so, `isle` commands will be available globally wherever npm is available.

## Command Structure

Isle commands are organized into three main categories:

### `isle app <command>` - Mesh Application Management
Manage mesh applications, services, and configurations.

### `isle router <command>` - Router Management
Manage OpenWRT virtual routers for network isolation.

### `isle agent <command>` - Agent Management (Coming Soon)
Automatic bridge management between containers and routers.

## Quick Start

### 1. Initialize a Mesh Application

```bash
# Convert existing docker-compose
isle app init -f docker-compose.yml -d myapp.local

# Or create from scratch
mkdir my-mesh-app && cd my-mesh-app
isle app init -d myapp.local
```

### 2. Manage Application Services

```bash
# Start services
isle app up --build

# View logs
isle app logs [service-name]

# Stop services
isle app down

# List running services
isle app ps
```

### 3. Setup Router (Optional)

```bash
# Install router dependencies
sudo isle install router

# Initialize secure router
sudo isle router init

# Check router status
isle router status
```

## Common Commands

### Application Commands
- `isle app init` - Initialize new mesh-app
- `isle app up` - Start services
- `isle app down` - Stop services
- `isle app logs` - View logs
- `isle app discover` - Discover .local domains
- `isle app config` - Manage configuration
- `isle app scaffold` - Convert docker-compose

### Router Commands
- `isle router list` - List routers
- `isle router up <name>` - Start router
- `isle router down <name>` - Stop router
- `isle router status` - Show status
- `isle router help` - Detailed router help

### Global Commands
- `isle install [target]` - Install dependencies (app/router/agent/all)
- `isle help` - Show help message
- `isle uninstall` - Uninstall the CLI globally

## Getting Help

For detailed command information:

```bash
isle help              # Overview
isle app help          # All app commands
isle router help       # All router commands
isle agent help        # Agent commands (coming soon)
```

## Migration from Old Commands

If you used Isle CLI before the restructuring:

```bash
# Old command          →  New command
isle init              →  isle app init
isle up                →  isle app up
isle down              →  isle app down
isle logs              →  isle app logs
isle discover          →  isle app discover
isle config            →  isle app config
```

All old commands now require a namespace specifier (`app`, `router`, or `agent`).

## Documentation

### Isle image registry

`sudo isle registry setup` configures the existing registry service on the Isle
CA host. It selects the actual `br-mgmt` IPv4 address; `--address <IPv4>` selects
another address already assigned to that host. `--port` defaults to 5000. A
missing or ambiguous address refuses setup. Offline setup requires the exact
`registry:2` image recorded in the installation medium. New media builds include
that image; previously built media are not retroactively changed.

`isle registry verify` (or `--port <port>`) checks normal DNS and trusted HTTPS
without changing the host. Run it on each intended publisher/consumer. This
checks API access; a successful Docker push/pull remains a separate distribution
test. Setup is privileged and recreates the registry container while retaining
its data directory; it is not a read-only inspection command.

Offline Mesh App deployment also accepts an explicit immutable image reference
such as `registry.isle:5000/engines/board@sha256:<64-hex-digest>`. It reuses an
exact cached repository digest, or verifies the registry with the Isle root
before pulling that exact reference. Compose then runs with `--no-build --pull
never`. Mutable tags and external registries remain restricted to images
verified against the medium. This supports later Isle-published apps without
treating offline mode as a ban on the Isle's own local supply; it does not
automatically publish images or choose a compatible app version.

### Member service certificates

Normal app deployment uses authenticated automatic enrollment. On the core,
`sudo isle certs setup-authority` enables the bounded signing service behind
`https://certs.isle`. Pair a named member once with
`sudo isle certs authorize-member <device> --output <private-file>` and transfer
that file privately to the corresponding device. There, run
`sudo isle certs pair <private-file>` (or pass `--enrollment <private-file>` to
`isle join`). Delete the transfer copy after pairing. The credential is not a
CA key; it is scoped to one member and must never enter a package or repository.

Subsequent `isle app deploy` and Polari instance deployments generate a local
service key, send only a CSR over verified HTTPS, and install the verified chain.
The authority reserves each name to its authenticated member; another member
cannot claim it. Core service names are reserved. Renewal/retry keeps the member
key and ownership. `sudo isle certs revoke-member <device>` prevents further
issuance; already issued leaves remain valid until expiry. Pairing is an explicit
core authorization step, not an automatic grant to every device on the LAN.

Members keep their service private keys locally. For an operator-mediated
certificate exchange, use the existing Isle root trust and transfer the public
CSR and chain over a verified channel. On the member:

```bash
sudo isle certs request board-engines.isle --output /tmp/board-engines.csr
```

On the Isle CA host, the operator must verify the requesting device, CSR SHA256,
and ownership of the service name before signing. This command is explicit
authorization for that name; it does not perform automatic device enrollment or
reserve DNS ownership:

```bash
sudo isle certs sign /path/to/board-engines.csr --domain board-engines.isle --output /tmp/board-engines-chain.pem
```

Return only the public chain to the member:

```bash
sudo isle certs install board-engines.isle --certificate /path/to/board-engines-chain.pem
sudo isle certs reload
```

Import checks the Isle CA, exact service SAN, validity and matching local private
key before replacing the certificate. Renewal requests reuse the existing key.
Existing exchange output files are never overwritten. Neither private service
keys nor CA signing keys should be transferred. These commands do not register
the app or its DNS name; verify the actual app through trusted HTTPS from another
device after registration. For Polari instances, `isle polari instance verify
<name>` checks frontend and backend without redeploying.

`sudo isle certs check <domain>` checks an installed chain and matching key
without changing them. Deployment and `.isle` agent registration use `certs
ensure`: reuse a valid service identity, issue locally on the CA host if needed,
or stop with CSR instructions on a member. A member's failed certificate check
does not publish a new registry entry or start new app containers.

### Applying app placement

`isle polari app ensure <app>` validates the complete typed placement plan
before applying it. It executes only supported module-add and instance-deploy
actions, not the server's display command strings. Targets must be configured
on the executing device. Multiple missing modules needing a new instance are
grouped into one deployment. Failed steps stop the operation, and completion
requires a fresh placement result. This confirms module placement, not worker,
data-migration or physical-hardware readiness.

Mesh App engine declarations now default to the published HTTPS `.isle` domain,
so another device can resolve the provider. Existing explicit endpoint overrides
are retained. Previously written `engine.json` files are not migrated by a CLI
upgrade; review/redeploy those declarations as part of the app's placement work.

For comprehensive documentation, see:
- [CLI-ARCHITECTURE.md](CLI-ARCHITECTURE.md) - Complete architecture
- [QUICK-REFERENCE.md](QUICK-REFERENCE.md) - Command reference
- [EXTENSION-GUIDE.md](EXTENSION-GUIDE.md) - Adding new features
- [UNINSTALL-GUIDE.md](UNINSTALL-GUIDE.md) - Uninstallation guide

### Optional Keycloak sign-in

An isle names at most one Keycloak. It may run on any device of the isle. It is named on the core:

```
isle polari security provider
sudo isle polari security provider https://auth.example.isle/realms/Polari
sudo isle polari security provider none
```

The first form shows it (on a member: what the core publishes). Naming one asks that Keycloak for its
discovery document over verified TLS and refuses when it does not answer or names itself differently.
Naming one switches nothing on and changes no backend rule. The backend of the core is told where to check a
sign-in token at the next `sudo isle reconcile` (which restarts it once); a backend on another device when its
instance is deployed or updated. And `enable` and `require` below write the address into the
frontend's file. With none named, everything an isle does without sign-in works as before.

What on the isle needs a signed-in person is a list in the same record, changed on the core:

```
sudo isle polari security provider require releases
sudo isle polari security provider unrequire releases
```

`releases` is the one thing that can be named today: a release packet a member offers to the core (start
setting `any-device`). Nothing is required until it is named, and it cannot be named before a Keycloak is.
`provider none` is refused while the list is not empty. An offered packet cannot carry a sign-in yet, so
while `releases` is required the core answers every offer `refused` before it fetches anything, and a member
is told so before it offers. A release typed on the core itself is not asked for a sign-in: sudo on the core
is what allows it. A record the core cannot read counts as required.

On the device hosting a Polari frontend, manage its deployment-owned setting:

```
sudo isle polari security disable --config ~/polari-isle/runtime-config.json
sudo isle polari security enable --config ~/polari-isle/runtime-config.json
isle polari security status --config ~/polari-isle/runtime-config.json
```

Reload the frontend after changing the setting. Disabling retains provider details,
skips OIDC startup checks and hides sign-in controls. It does **not** disable backend
access control or make protected operations available anonymously. Enabling requires
an HTTPS authority and client ID; the Keycloak service must also be deployed and healthy.
Existing configurations without `keycloak.enabled` retain their prior enabled behavior.
New lean core configurations explicitly disable the absent provider.

Apps/features that require sign-in must declare their dependency in deployment configuration:

```
sudo isle polari security require --feature private-files --config /path/to/runtime-config.json
```

This enables Keycloak and records the feature in `keycloak.requiredBy`. Disable is
refused while any dependency remains. After removing the feature, use `release` with
the same name. An inconsistent required-but-disabled config raises a critical service
configuration warning in the frontend. These declarations are deployment metadata;
module installers must register/release them, and backend routes must enforce their
own authentication. This does not automatically discover undeclared module dependencies.
Changes preserve the bind-mounted file inode and retain the first previous configuration
in `runtime-config.json.before-security`. The Isle App Security tab displays status and
commands; changing the shared deployment requires host administration, not a browser preference.

The native Security tab additionally supports a local PolicyKit toggle. It calls
only `/usr/share/isle-mesh/isle-cli/scripts/security-toggle.py` with a fixed
`enable` or `disable` action and the shell's configured HTTPS frontend origin.
The page cannot supply a command, config path, or remote device. The dedicated
`org.islemesh.keycloak.toggle` action requires fresh administrator authentication
from an active local session (`auth_admin`, not `auth_admin_keep`). Cancelling or
refusing authentication leaves the setting unchanged. The native shell accepts
these calls only from its configured frontend's main frame.

The helper discovers one locally registered frontend config, refuses symlinks and
hardlinks, locks and re-reads the file, and enforces requiredBy before changing it.
User-owned core configuration is written after dropping root privileges to the
requesting desktop user. System deployment configuration must be root-owned and
not group/world writable. Enabling checks trusted HTTPS OIDC discovery and its
issuer before saving; it does not deploy Keycloak. The frontend reloads only after
successful readback. This toggle changes sign-in availability; it is deliberately
separate from `POLARI_APP_PERMISSIONS` and backend enforcement policy.


### Device-backups

A device-backup is one backup of one machine taken at one time: a folder `device-backups/<device>/<id>/` with one
archive per item and `device-backup.json`.

    isle backup plan [--json]          read-only: what one would hold, what is left out and why, where it would go
    sudo isle backup create --yes      journaled: write one, re-read every file, then keep two
    sudo isle backup list [--json]     the device-backups of this machine
    sudo isle backup verify <id>       re-read every file of one against its record
    sudo isle backup release-hold      lower a write gate that an interrupted device-backup left up

- It holds the isle's settings folder (without the package store), the isle's state folder (without release files,
  operation journals and the device-backups themselves) and the data volume of every Polari instance the isle CLI
  deployed on the machine. A running instance is copied only behind the engine's write gate. Every other named
  Docker volume is listed as not covered.
- It lives on the partition the machine records with the role `backup` (`isle storage partition list`); a machine
  that records none keeps it in its state folder. A recorded `backup` partition that cannot be used is a refusal.
- It is complete only after every file was read back and matched. Two are kept per device: `release` (the newest)
  and `long-term` (the previous one takes that place when the long-term one is 7 days old or older, otherwise the
  previous one is deleted). Nothing is deleted before the new one is complete.
- Nothing is encrypted and nothing is sent anywhere.
- `isle release run` takes one as a device step: `{"verb": "backup"}`. The run stops there if it is not complete.

### Starting a release run from a member (start setting `any-device`)

By default a release run is started on the core only. The core can allow members to start one:

    sudo isle release setting any-device     core: allow it (prints the cost, switches the core's timer on)
    sudo isle release setting core-only      core: the default again
    sudo isle release run <packet-dir>       member: check the packet, offer it, wait for the core's answer
    isle release offer                       member: what this device offers and what the core answered
    sudo isle release offer withdraw         member: take the offer down and remove its copy
    isle release intake                      whether the core takes packets from members, and its answers

- The member serves a checked copy of the packet as the isle app `release-offer-<its paired name>`, from the agent
  image it already runs. Nothing is pulled.
- The core fetches only from a name the certificate authority issued to that very member, over TLS checked against
  the isle's root, checks the packet again and runs it as if it had been typed on the core. The receipt names the
  offering member.
- Where the core asks: at the address the member's agent reported to the core's device list, else at the one the
  router leased to it. The address only says where to ask; the certificate is what proves the member.
- Cost: root on any paired member can then release to the whole isle, and every device on the isle can read an
  offered packet while it is offered.
- An isle that requires sign-in for releases (`sudo isle polari security provider require releases`) takes no
  offer at all until an offer can carry a sign-in: the core refuses it before fetching, and the release is
  started on the core.

### Shared local operations

`sudo isle store install <catalog-name> --yes` and the native Install button
invoke the same store operation. The command prints `Operation: <id>`; inspect
it with `isle operation status <id>` and retry with
`sudo isle operation retry <id>`. Every journaled operation that ends other than
`succeeded` prints that command with its id (on stderr). Retry keeps the original catalog response and
replays its plan through the existing shared deployment commands. Successful
operations are not replayed by retry; use a new install for an intentional
upgrade. Per-app locks prevent concurrent attempts. A killed process is reported
as interrupted, never completed. Journals persist in `/var/lib/isle-mesh/operations`.

`isle app status <name>` reports configuration, actual runtime and trusted local
HTTPS evidence separately. A directory alone is staged, not deployed. HTTPS
checks currently require success from the registered domain's root path;
applications with a different health endpoint need catalog health metadata.
This local probe is not evidence of cross-device reachability.

`isle update plan` reviews the exact published package manifest and actual local
dpkg status. `sudo isle update apply <release-id>` verifies HTTPS artifact hashes,
package names and versions, refuses implicit downgrades, installs with
`apt-get --no-remove`, and reconciles owned local host routes and reporting.
`sudo isle reconcile` reruns that reconciliation without recreating membership or
applications. These actions share the operation journal and native bridge.
`sudo isle update apply` and `sudo isle reconcile` also look at the systemd units of every storage dataset bound on the device (the gate that hosts it, its
sync service, its sync timer). A unit file whose text differs from what the installed CLI writes is rewritten,
and the replaced text is kept in `/var/lib/isle-mesh/storage/<dataset>/units-replaced/<time>/`. Nothing is
enabled, started or restarted: a rewritten unit is used from its next start. A missing unit, a unit that is not
enabled and a hand override are named in the output and left as found. `isle storage units check
[--dataset D]` shows the same comparison at any time, and `sudo isle storage units apply --dataset D
[--overrides keep|remove]` rewrites, enables and (with `remove`) drops the overrides. This step never fails an
update.
An enabled unit has not shown that it runs. After `mark-migrated`, the first sync that the sync timer starts on the
hosting device restarts the hosting unit through systemd, reads that it is active and reports that to the core once;
`sudo isle storage cutover` refuses until the core holds that report. `sudo isle storage units prove --dataset D`
on the hosting device does it now instead of within the timer's 10 minutes, and `isle storage status --dataset D`
shows the result as `unitProof`.
An update does not rebuild/recreate the agent image or migrate core runtime
images; those remain distinct coordinated release actions, not implied by package
success. No general system upgrade or rejoin is performed.

Native operation routes: `device.updatePlan`, `device.updateApply(releaseId)`,
`device.reconcile`, `operation.status(id)`, and `operation.retry(id)` use fixed
argument arrays; there is no arbitrary command/path bridge. Administrator
refusal is returned to the caller without a success claim. The existing
`store.install(name)` boundary is preserved.

### Member identity reconciliation

CLI 205 refuses updates, deployments and reconciles when the two saved member names
disagree: `/etc/isle-mesh/device-name` (canonical; mounted into the agent and reported
to the core) and `/etc/isle-mesh/agent/remote/hostname` (the registration name saved by
the original `isle join`). `isle identity status` shows both. `isle identity plan
--expected-legacy <saved-registration-name> --canonical <device-name>` is read-only and
states what would change. `sudo isle identity reconcile ...` with the same two explicit
values is a journaled operation: it refuses when the saved name is not the stated one,
when `device-name` is missing or differs from `--canonical`, or when a hosting credential
names another device; otherwise it copies the old file beside the original (mtime kept),
rewrites it atomically, re-runs the preflight and journals a receipt in the operation
directory and `agent/remote/identity-reconcile-receipts.jsonl`. MAC, DHCP lease,
discovery, interface, NetworkManager profile, certificates, enrollment credential and the
OS hostname are never modified (the OS hostname is not an Isle identity). It never joins,
rejoins or renames a device at the core. Afterwards retry the refused operation with
`sudo isle operation retry <id>`.

### Storage placement inspection

`isle storage list` returns a timestamped JSON inventory, including unmounted
partitions and filesystem UUIDs. It does not mount, format or alter any disk.
Use filesystem UUIDs rather than changing device names such as `/dev/sdc1`.

Before a planned copy, on the destination device run:

```sh
isle storage check --path /mnt/storage/isle-repository --uuid <filesystem-uuid> --required-bytes <copy-budget>
```

This read-only operation uses the same local preflight helper available to
placement code. It refuses a different or absent filesystem, fallback to the
root filesystem, symlink paths, read-only mounts and insufficient capacity.
Success reports `localMountVerified`, not approval to migrate. It does not prove
write permissions, persistent boot ordering or safety after a later unplug.
It creates no directories. Errors return JSON `ok:false` and a nonzero exit.

These inspection commands need no administrator privileges or mutation journal.
Repository migration and native planning execution are not implemented by these
commands; keep the existing serving source until a coordinated migration has
verified its replacement. No storage inspection button is exposed yet.

For the public APT/artifact repository, `sudo isle apt-repo snapshot` prints a
deterministic content manifest while holding the same publication lock as APT,
runtime-image and catalog publication. The fixed source is the public `repo/`
directory, not its sibling signing-key directory. Store the output outside that
source and check command success before using it. Creating the lock may require
administrator rights; no repository contents are changed.

`isle storage snapshot --path <absolute-directory>` provides the same inventory
for other app data, but the caller must first quiesce that app's writers. These
public-content inventories reject symlinks, hardlinks, special files, nested
filesystems, hidden entries and incomplete `.part`/`.partial` files. They stream
file hashes with bounded memory and detect observed changes during hashing.
They are not live-database backup tools or point-in-time filesystem snapshots.

After a separately coordinated copy, the shared verification command is:

```sh
isle storage verify --path <destination> --manifest <snapshot.json> --sha256 <trusted-manifest-sha256>
```

The digest must come through the authenticated plan or operator channel, not
from an untrusted copy alongside the files. Verification checks the manifest
digest and exact tree contents, including extra or missing files. This proves
content equality only. It neither verifies repository signatures nor changes
DNS, starts an app, authorizes removal, or records migration completion. Both
snapshot and destination verification remain preparatory shared operations;
copy, mount lifecycle, app deployment and verified cutover are separate work.

### Supported operating systems and dependency sets

The operating systems the isle is run on are written down in one file of this CLI,
`apt/supported-os.json`. It is data, not code: one entry per system with its release name, its
architectures, how far it is proven as a core and as a member (`live-verified`, `source-tested` or
`not proven`), the image its dependency set is resolved in, and what differs from the other systems
for packages (for example a library that has another name there). A system is added by adding its
entry after it has been proven; no script decides by a release name written into it.

```sh
isle os list --table       # the record
isle os check --table      # this machine: its system, whether the record has it, whether the core serves its dependency set
isle os devices --table    # what every machine of the isle last reported as its system
```

All three only read; without `--table` they print JSON. `isle os check` exits 0 when this machine's
system and architecture are in the record and 1 when not. Every machine sends its system
(`host.os_release`, for example `ubuntu-22.04`) and its release name (`host.os_codename`) with its
normal device report; `isle os devices` shows them from the core's device list, so a machine with an
older CLI shows as "not reported".

The isle package repository has two kinds of index. The main index holds only `isle-*` and `polari-*`
packages and is what a device's own apt reads. The operating system packages those need offline are
**dependency sets**. A set is built for one release, so each release has its own index,
`deps/<release name>/`, and only `isle apt install` reads it, on a device of that same release. The
package files of all sets lie together in the repository; an index only lists them.

```sh
sudo isle apt-repo publish --dependencies-from <directory> [--dependencies-release <release name>]
```

The release a set is filed under is decided before anything is changed, in this order: the release
typed with `--dependencies-release`; the marker file an offline medium writes beside its packages
(`<system id>.txt`, for example `ubuntu-22.04.txt`); else this core's own release. A typed or marked
release has to be in the record, and a typed one may not disagree with the marker; otherwise the
publication is refused and the repository is unchanged. A later publication keeps every package file
in each set that already lists it, so adding a set for one release never empties another. A package
file that no set lists is filed under this core's own release.

### Checked certificates and the pinned router key

Every isle script checks who it is talking to. Two files decide how, and no script decides it for itself.

**HTTPS (`scripts/lib/tls.sh`).** A shell script calls `isle_curl` in place of `curl`.

| Setting | What is checked |
|---|---|
| nothing set | the service's certificate, against this isle's root certificate and no other (the system's certificate store where a machine has no isle root yet) |
| `ISLE_TLS_CA=<pem file>` | the certificate, against that file and no other: the setting for a private or self-signed deployment that is not an isle |
| `ISLE_TLS_INSECURE=1` | nothing. It is said once per run and again at every call, also when the call hides its own errors |

With both set, the file wins and the ignored setting is said. `isle_curl_site` is the same for a site that is not one
of the isle's own services (an app's own name): with nothing set it uses the system's certificate store.

Some fetches happen before a machine trusts anything, so they cannot be checked by a certificate: the root
certificate itself (accepted only by the fingerprint a person typed, or by the signature of the archive key already
installed) and the join instructions a core prints (the file is checked by its sha256 before it is run).
`scripts/test-unverified-calls.py` lists every one with its reason, and fails when a script adds another.

**ssh to the router (`scripts/router_ssh.py`).** Every script reaches the router at the address this machine's record
names, and only when the router shows the key that was pinned on this machine. There is no first-contact trust: a
machine with no pinned key connects to no router, and a router that shows another key is refused.

```bash
sudo isle router key-status      # the pinned fingerprint, and whether the router shows that key now
sudo isle router trust-key <router-address> <public-key-file> <SHA256:fingerprint>     # pin it, or pin it again
```

The public key and its fingerprint are read on the router's own console, not over the network. Pinning again
replaces the line of that address, which is what a router that was installed again needs.

### Storage devices, partitions and roles (st-1)

A machine's storage is described by three things. A **device** is a disk, named by its hardware serial and
declared with a class: `fixed` (stays in the machine), `dock` or `flash` (may be away). A **partition** is a
filesystem, named by its filesystem identifier, never by a name such as `sdc1`. A **role** says what a partition
is for: `package-repo`, `distribution`, `registry`, `blob`, `git`, `assets`, `backup`, `scratch` or `transfer`.
A partition carries one role or more than one. Roles on one partition share its space: nothing is set aside for
each of them.

```
isle storage device list --table
sudo isle storage device register <serial> --class fixed|dock|flash
isle storage partition list --table
sudo isle storage partition assign <filesystem-identifier> --role <role>[,<role>...]
isle storage partition check --table
isle storage role list --table
isle storage role show <role> --table
isle storage role list --isle --table
isle storage role show <role> --isle --table
```

Without `--table` every listing is a JSON document. The two `sudo` verbs print the command to type when they
are run without root, after every check has passed.

`partition assign` adopts a filesystem that already exists and is mounted. It writes one file, the marker
`isle-partition.json` at the root of the partition, and one entry in this machine's record
(`/etc/isle-mesh/storage/device-records.json`). It creates, erases, formats, mounts and moves nothing, and it
leaves every other file on the partition alone. The marker travels with the disk, so a partition marked on one
machine is recognised on another: `partition check` shows it as `not recorded here`, and `assign` with the same
roles records it without rewriting the marker. A link at the marker's name is never followed.

A role is added by assigning it: `assign` on a partition that already carries roles keeps them and adds the one
typed (several are typed with commas between them, `--role backup,git`). The roles a partition already carries
are never taken away by `assign`. With one role the marker and the record hold `role`, as before; with more
they hold `roles` (all of them, in the order they were given) and `role` (the first), so a CLI from before reads
a role it knows. When the marker and the record name different roles (the disk was given a role on another
machine), `assign` joins them only when every one of those roles is typed, and prints the command that does it:
no role is taken over unasked. A marker that is changed keeps who wrote it first and gains `changedAt` and
`changedOn`.

`assign` refuses, and writes nothing, when: the device is not registered or reports no serial; the partition
carries the running system; its marker and this machine's record name different roles and not all of them were
typed (taking a role away is `partition retire`, which is not built yet); a role is typed twice; a role has to
be written to the marker and the filesystem is mounted read-only; the marker or the record changed while the
command ran; two partitions report the same identifier; the marker names another filesystem (the filesystem
is a block copy); a file of that name exists that is not a marker; or the filesystem is not mounted whole where
it can be reached (only a folder of it is mounted, or another mount covers it).

`partition check` compares the record with the marker on each recorded partition and exits 1 when one needs
attention: `marker missing`, `marker differs`, `marker cannot be read`, `device not registered`,
`on another device`, or a partition of a `fixed` device that is away. A partition of a `dock` or `flash` device
that is away is reported and is not an error. The marker and the record agree when they name the same roles,
in any order. `role show <role>` lists the partitions that carry a role with their device, free space and the
datasets bound on them; a partition with several roles is listed under each, and `role list` counts its free
space under each (it is one space, and the listing says so).

These records describe one machine, and each machine keeps its own. With `--isle`, `role list` and `role show`
print what every machine of the isle reported, side by side, from any machine: each machine adds its partitions
that carry a role (role, filesystem identifier, class, size, free space, state, datasets; no hardware serial) to
the device report it already sends to the core, and the view reads the core's device list. A partition with
several roles is reported once per role, so the report has the same shape as before. It shows when each
machine reported, and names the machines that sent no storage report (an older isle CLI) or one this version
cannot read. It is a report, not a check: whether a partition is really there is asked on the machine itself with
`partition check`. Polari reads the same device list; there is no second store.

The roles are not yet part of the core's placement record, and no verb reads a role to decide where data goes.
Not built: `device health|retire`, `partition create|retire`.
Tests: `python3 isle-cli/scripts/test-storage-roles.py`, `test-storage-isle.py` and `test-storage-devices.py`
(fixtures only).

### Storage migration contract (stm-1)

`scripts/storage_contract.py` holds the typed contract for moving a dataset onto another device's
filesystem (`docs/STORAGE-MIGRATION-CONTRACT.md`). It is pure stdlib code: it validates the plan item's
`storage` block (unknown keys are dropped), builds the planned action, the `storage migrate|cutover|rollback|sync`
argv, the receipt skeleton and the binding record, and parses requests for `operations.py` later.
The plan digest (D6) is the sha256 of the canonical JSON (`sort_keys`, `(",", ":")`, `ensure_ascii`) of the
execution tuple `{planId, revision, itemId, device, originDevice, app, state, storage (whitelisted),
hostingRelease {name, version, manifestSha256}}`; any change to any of them changes it. Requests (D5) are
`storage migrate --dataset D --plan P --revision N --item I --digest H` and `storage sync --dataset D` on the
target, `storage cutover|rollback --dataset D --generation G` on the core; `lock_name()` and `journal_name()`
derive `app-storage-<dataset>.lock` and `storage-<dataset>` from the parsed tokens.
The backend carries the same function and both check `scripts/fixtures/storage-digest-vector.json`.
Nothing executes yet: no command, stage, mount, copy or DNS change is wired to this module.
Check it with `python3 isle-cli/scripts/test-storage-contract.py`.

### Placement record and content manifests (stm-3)

`storage_placement.py` keeps the core-side record of which device hosts a dataset
(`/etc/isle-mesh/storage/placement.json`): one entry per dataset with `device`, `domain`, `sourcePath`,
`state`, `generation`, the plan `{id, revision, digest}`, a `history` of every transition and the
source/target addresses. States: `authorized` → `migrated` → `cutover` → `hosting`, plus `recovery-needed`.
Every change is a compare-and-set on the current state (and, where given, the generation) under a lock;
`transition()` is the one function that moves an entry, and only `migrated → cutover` bumps the generation.
Core-side verbs (root): `sudo isle storage authorize --dataset D --device X --domain Y
--source-path P --plan-id I --plan-revision N --plan-digest H [--replace]` creates the entry;
`mark-migrated --dataset D --digest H` (authorized → migrated, after the target's receipt);
`accept --dataset D --generation G` (cutover → hosting, after a neighbour witness).
The cutover and rollback transitions belong to the executor slice and are not exposed here yet.
While an entry exists in any state, every publisher (`apt-repo.sh publish`, `isle store publish`,
`isle update publish-runtime`) also writes `repo/snapshot/<sha256>.json` (the content inventory plus a
`binding` block: dataset, source device, taken-at, lock path, plan id) and `repo/snapshot/latest.json`,
under the publication lock it already holds (the helper never re-takes it: a nested flock would deadlock).
`sudo isle storage publish-manifest --repo PATH` prepares a manifest with no placement record at all.
The repo's top-level `snapshot/` directory is reserved for these manifests and excluded from content
inventories; a failed manifest step prints `[storage] manifest not published: ...` and never fails the
publication itself. None of this moves data; nothing executes a migration yet.
