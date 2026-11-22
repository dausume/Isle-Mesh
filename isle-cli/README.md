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

### Building the packet of a CLI release

One command turns one commit of this repository into the folder `isle release run` takes. It needs no sudo, asks no
core anything, and publishes and installs nothing:

    isle release build <out-dir> --previous <dir>
    isle release build <out-dir> --dependencies <set dir>
    isle release build help                  every option

- `--previous` is the folder `members` of the build before. The next version numbers are read from its packages
  (the last number and the release number each go up by one), and the member packages a CLI release does not
  rebuild are taken from it unchanged.
- The commit is built, not the files on disk (`--commit`, default `HEAD` of `--source`). Files that are changed but
  not committed are counted and said to be left out.
- A CLI release carries three packages: the CLI, the store, and the combined package a core installs. The command
  builds all three, then unpacks the CLI package and the combined one and compares every file the CLI package packs
  with the commit, byte for byte. A difference is a refusal.
- The packet names no device unless `--device` does: it is then for the core and every member the core heard from
  in the last 24 hours. `--backup` makes every device take a device-backup first. `--verify <url>` adds a check.
- It is built on a machine of the isle the release is for, because the store package carries that isle's root
  certificate.
- It leaves `packet/` (copy this to where the run is typed), `members/` (the `--previous` of the next build),
  `source/`, `build.log`, and `build.json`: the commit, the versions and every checksum.
- A folder that already holds something is never written over.

The second form builds a packet that holds one dependency set and no package of the isle. Nothing is built for
it: the set is checked and copied into the packet. `--dependencies <set dir>` can also be added to the first form,
and the release then carries the set with it.

- A set is a folder of operating system packages with one file `<system id>.txt` that says the system it is built
  for (see "Supported operating systems and dependency sets" below). It is refused before anything is written when
  it says no system or more than one, says a system the record does not list, holds no package, holds an `isle-*`
  or `polari-*` package, holds anything that is not a regular file, or differs from its own `SHA256SUMS`.
- Every file of the set is listed in the packet's `SHA256SUMS`, so the run id names every byte of it.
- In the run, the core adds the set first, with `isle apt-repo publish --only-dependencies`, and waits until its
  index lists every package file and every file is served. Only then is anything else of the packet published. The
  release the core announces stays the one it is.
- A packet with a set alone gives the devices no step: no device installs anything. The core's own wait is the
  proof that the set is served. With `--device`, each named device also asks for the index of the set from where
  it is; `--verify` adds a check for those devices and is refused without `--device`.
- The core's installed CLI has to know this kind of packet. A core whose CLI is older refuses the packet and
  changes nothing, so a set cannot travel in the same packet as the CLI release that teaches the core the kind.
- The core copies a packet before it runs it, so it needs the size of the set and 256 MiB more free where it keeps
  its runs. `isle release check <packet>` on the core says so.

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

The scripts that build and prove an offline medium read the same record, by system id, instead of
keeping a list of their own. A system the record does not have is refused with the ids it has:

```sh
python3 scripts/supported_os.py base-image ubuntu-22.04            # the image that system's packages are resolved in
python3 scripts/supported_os.py package-names ubuntu-22.04 <list>  # a list of package names, with the names that system uses
python3 scripts/supported_os.py offline-packages ubuntu-22.04 install      # the offline dependency list, with that system's names
python3 scripts/supported_os.py offline-packages ubuntu-22.04 cache-only   # its second part
```

The dependency lists are written once, with the names of the newest system; `package-names` replaces
only whole names that the record lists for the asked system and leaves every other character as it is.

The operating system packages an isle needs when it is installed with no internet are one list,
`apt/offline-packages.json`, beside the record. It has two parts: `install` (packages an offline
install puts on the machine) and `cacheOnly` (packages that are only carried, for a later step that
installs them). An entry `a | b` means either package serves. The builder of an offline medium and
`isle os dependencies build` both read this one list.

#### Building a dependency set

```sh
isle os dependencies build <out-dir> --system <system id> --packages <dir> [--bundle-builder <file>] [--cache <dir>]
isle os dependencies check <set dir>
```

`build` is run without sudo and changes nothing on the isle: it publishes nothing. It makes the
folder that `isle release build <out-dir> --dependencies <set dir>` takes, for one supported system:

- `--packages` is a folder of the isle's own packages (the folder `members` that a release build
  leaves). The set is resolved from what those packages declare and from the offline list above, in
  a fresh container of that system's image, so nothing installed on the building machine decides it.
- The resolving and fetching is done by the suite's `build-offline-bundle.sh` (found beside the
  checkout, or named with `--bundle-builder`). Every file is checked against the size and sha256
  that the signed package index of the system's archive names.
- `--cache` names a folder of package files kept between builds. A file from it is used only when
  it has that size and sha256; what is fetched is added to it. Without it everything is fetched,
  about 500 MiB for one system.
- The set holds the package files the resolve names and nothing else, the three `CLOSURE_*` lists
  of the resolve, `<system id>.txt` and `SHA256SUMS`. No `isle-*` or `polari-*` package is in it.

It is refused, before anything is fetched or written, when run as root, when `<out-dir>` exists and
is not empty, when the system is not in the record, when `--packages` holds no package or a package
that is not the isle's, or when the builder is not found. If the builder stops, its last lines and
the place of its log are printed and `<out-dir>` is not written; the work folder beside it
(`<out-dir>.work`) is removed by the next build.

`check` reads a set and changes nothing: it has to name one system, hold no package of the isle, and
every package file has to be the one its `SHA256SUMS` lists and the one the resolve's list
(`CLOSURE_METADATA.json`) names by size and sha256, with none missing and none added.

The isle package repository has two kinds of index. The main index holds only `isle-*` and `polari-*`
packages and is what a device's own apt reads. The operating system packages those need offline are
**dependency sets**. A set is built for one release, so each release has its own index,
`deps/<release name>/`, and only `isle apt install` reads it, on a device of that same release. The
package files of all sets lie together in the repository; an index only lists them.

```sh
sudo isle apt-repo publish --dependencies-from <directory> [--dependencies-release <release name>]
sudo isle apt-repo publish --only-dependencies --dependencies-from <directory> [--dependencies-release <release name>]
```

The first form publishes the isle's own packages as well, as every `publish` does. With `--only-dependencies`
no isle package is looked for and the set is added alone: it is refused with `--from`, refused without a set, and
refused when the set holds an `isle-*` or `polari-*` package, so the release the core announces cannot change. This
is the form a release run uses for a set a packet carries.

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
sudo isle storage partition retire <filesystem-identifier> --role <role>[,<role>...] [--record-only] [--leave-data]
sudo isle storage device retire <serial>
sudo isle storage device health <serial> [--table]
isle storage partition create <serial>
isle storage partition create <serial> --partition <partition-identifier> --role <role>[,<role>...] [--label <label>]
isle storage partition create <serial> --whole --role <role>[,<role>...] [--label <label>]
sudo isle storage partition create <serial> --partition ...|--whole --role ... --expect <plan-id>
sudo isle storage partition mount <filesystem-identifier> --at <folder>
sudo isle storage partition unmount <filesystem-identifier> [--with-datasets]
isle storage role list --table
isle storage role show <role> --table
isle storage role list --isle --table
isle storage role show <role> --isle --table
isle storage kind list --table
isle storage need list --table
isle storage association list --table
isle storage slot list --table
isle storage slot check [--app <app>] --table
sudo isle storage slot declare <app> --from <release.json>
sudo isle storage slot bind <app> <slot> --partition <filesystem-identifier>
sudo isle storage slot unbind <app> <slot>
```

Without `--table` every listing is a JSON document. The `sudo` verbs print the command to type when they
are run without root, after every check has passed.

`partition assign` adopts a filesystem that already exists and is mounted. It writes one file, the marker
`isle-partition.json` at the root of the partition, and one entry in this machine's record
(`/etc/isle-mesh/storage/device-records.json`). It makes, erases, formats, mounts and moves nothing, and it
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
typed (taking a role away is `partition retire`, below); a role is typed twice; a role has to
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

A role is only a purpose. Three more things are named apart from it, each a short fixed list, and a fixed table
in the CLI says which of them a role allows. No record and no marker holds them. `kind list` shows the kinds of
storage: what serves the data and so what the machine that holds it must have (`plain-folder` needs nothing
more; `file-pool` needs the file store's one program `weed`, or Docker with an image of it; `image-registry`
and `git` need Docker with the registry's or the forge's image), and whether this machine has it: `ready`,
`not ready`, or `not known` when Docker did not answer the user who asked. `need list` shows the distribution
needs, which say how the data has to reach the devices that use it (`kept`, `served`, `pooled`, `cached`,
`installed`, `carried`, `taken`) and what a plan has to do for each. `taken` is a copy on one device that owes its
edits back to its original; nothing takes or returns such a copy yet. `association list` shows what data can belong to
(the isle, one app, one machine, one release). `role list` names the kind of each role, and `role show <role>`
its kind, its distribution needs and what it belongs to.

After `partition assign` and `partition create` have given a partition its roles, they say what this machine
does not have for the kinds of those roles. That is never a refusal: the roles are recorded all the same, the
exit status stays 0, and nothing is installed. The look is two questions and nothing else: is a program on the
path, and does `docker image ls` show an image of a repository (any tag).

`partition retire` takes roles away again: afterwards neither the marker nor this machine's record names the
roles typed. A role that is not typed stays where it is, in the marker and in the record. When no role is left,
the record entry is dropped and the marker file is removed; that is the only file the verb ever removes. It
erases, formats, unmounts and moves nothing, and every other file on the partition stays. A role the partition
does not carry changes nothing and is not an error.

- `--record-only` changes this machine's record and never looks at the marker. It is for a partition whose
  marker cannot be changed: the disk is away or gone for good, it is not mounted whole, it is mounted read-only,
  the marker cannot be read, or the filesystem is a block copy. Without the option each of these is a refusal
  that names it. A marker left behind still names the role wherever the disk is plugged in next.
- `--leave-data` is needed in two cases, and without it the verb refuses and says why. One: the role `backup`
  is taken from the partition that holds this machine's device-backups; after that `isle backup` keeps new ones
  in the machine's state folder and no longer lists the ones on the partition (assigning the role again brings
  them back). Two: the last role is taken from a partition that a dataset is bound on.

`device retire` takes a device out of this machine's record. It does not look at the disk, so a disk that is
gone can be retired. It refuses while the record holds a partition with a role on that device, and prints the
`partition retire` command for each.

`device health` asks one disk for its own health report (SMART, through `smartctl`) and says what it found: the
disk's verdict, the hours it has been powered on, its temperature, and each count that should be zero and is not
(sectors replaced, sectors waiting to be read again, sectors that cannot be read, read errors that could not be
corrected; for an NVMe disk its critical warning, errors on the medium, spare space and wear). It exits 0 only
when the verdict is "passed" and no such count is above zero. It reads the report and starts no test on the disk.
A disk behind a USB bridge that does not answer is asked once more as a SATA disk; one that answers neither way
is a refusal with the tool's own words. When the device is registered the result (time, verdict, what needs
attention, hours) is kept in this machine's storage record under the device; a class change keeps it and
`device retire` drops it. It needs `smartctl` (package `smartmontools`), which the offline package list does not
name: on a machine without it the verb is a refusal that says so.

`partition create` makes a new filesystem (ext4, 1% kept in reserve) with the marker of its roles at its root
from the first moment, and records it on this machine. It is the one storage verb that erases. With the serial
alone it says what the device holds and which of its two ways fits:

- `--partition <partition-identifier>` takes one partition of the device that has no filesystem. The partition
  is named by its partition identifier (the `PARTUUID` of the partition table), because a partition with no
  filesystem has no filesystem identifier yet.
- `--whole` takes a device that has no partition table and no filesystem. It first gets a GPT partition table
  with one partition over the whole device, then the filesystem. A disk filled with a test pattern can make the
  system show partitions that are not there (they have no partition identifier); such a device counts as bare,
  and the plan says how many it shows.

It works in two steps, and only the second writes. Without `--expect` it reads the machine and prints the plan
(what is made, what is lost) and a plan id, with the command to type. With `sudo` and `--expect <plan-id>` it
reads the machine again, under the storage lock, and goes on only when the plan is still the same one: the
device's serial, model, size and class, the layout, the roles and the label all count. Then it asks the disk
itself (`blkid --probe`) at the device, at every partition the system shows, and at the new partition, and
stops when anything is recognised there that the machine did not report. The partition table is written with
`sfdisk` after a rehearsal that writes nothing, and with wiping switched off, so a filesystem from before that
begins where the new partition begins stays readable and stops the verb instead of being overwritten.

It refuses, and writes nothing, when: the device is not registered, is away, carries the running system or is
read-only; two devices report the serial; the partition (or, with `--whole`, the device or anything shown under
it) holds a filesystem, has something stacked on it, is in use or is read-only; with `--whole`, the device has
a partition table (adding a partition to a table is not built); two partitions report the partition identifier;
a role is typed twice; the label is not 1 to 16 letters, digits, dots, dashes or underscores; `sfdisk`,
`blkid` or `mkfs.ext4` is missing; or the plan id is not the one of the plan as it reads now. There is no
option to force any of these.

Once the first write has started a failure is no longer "nothing was changed": the verb prints `FAILED:` and
says what the device holds at that point (a partition table that may be partly written; a table with one
partition and no filesystem, with the `--partition` command that goes on from there; a filesystem that may be
partly made; or a finished filesystem that this machine does not record yet, with the `partition assign`
command that records it). It does not mount: the new filesystem shows in `partition check` as recorded and not
mounted, and `partition mount` is the verb for that. It needs `sfdisk` (package `fdisk`), `blkid` (`util-linux`) and `mkfs.ext4` (`e2fsprogs`); the offline
package list names none of them, and the three machines of the isle have them.

`partition mount` mounts a partition at a folder and keeps it mounted there. It writes one systemd mount unit
in `/etc/systemd/system` that names the filesystem by its identifier (never by a device name such as `sdb1`,
which can change), then reloads systemd, enables the unit and starts it. The unit is wanted by the device and
not by the start of the machine: the partition is mounted each time its disk is present, and a dock disk that
is away holds nothing up. systemd makes the folder when it is not there. Nothing is erased, formatted or
moved, `/etc/fstab` is read and never written, and no option of the mount is set (the filesystem's own
defaults). It refuses, and changes nothing: an identifier this machine does not see, or sees on two partitions;
a partition of the running system; a device that is not registered; a partition that is mounted already in any
other way (a mount made by hand or by `/etc/fstab` is not taken over); a filesystem or a folder that
`/etc/fstab` names; a folder that is `/`, is reached through a link, is not a folder, is not empty or has
something mounted on it; a unit file for that folder that this verb did not write; and a second folder for a
partition that has a unit of this verb already. Typed again for a partition it mounted, it says so and changes
nothing. When a step fails after the unit was written it prints `FAILED:` and says what is there (the unit
written, or written and enabled, and the partition not mounted by it); the same command goes on from there.

`partition unmount` stops that unit, disables it and removes the file. It only ever touches a unit that
`partition mount` wrote (the first line of the file says so): a partition mounted another way is refused and
left alone. A partition that holds a dataset of this machine is refused unless `--with-datasets` is typed,
because what serves the dataset is bound to the mount and stops with it; a storage slot bound to the partition counts the same way. The folder is left where it is, no data
is changed, and the storage records are not touched: the roles stay, and `partition check` shows the partition
as not mounted. It also removes the unit of a disk that is away. When systemd cannot stop the mount (something
on it is in use) it prints `FAILED:`, keeps the unit, and says that what depends on the mount may have been
stopped.

A storage slot is one storage need of one app: a folder the app sees, the role of the partition that has to hold
it, and whether the app writes to it. A release declares its slots in the list `storageSlots` of its release manifest
(`release.json`); each slot has a `name`, a `role` and a `target` (the plain absolute path of the folder inside
the app), and may have `service` (which service of the app sees it; the first one when left out), `writable`
(false when left out), `owner` (`<user>:<group>` as two numbers), `distribution` and `association` (one of those
the role allows; `kind list`, `need list` and `association list` show them), `copies` (1 or 2), `lifetime`
(`until-removed`, `release` or `scratch`) and `sizeBytes`. Any other key is refused.

`slot declare` reads that list from a release manifest and keeps it with the app
(`/etc/isle-mesh/apps/<app>/storage-slots.json`). It binds, makes and starts nothing. It refuses a release that
drops a slot which is bound on this machine, or declares it with another role: unbind it first. `slot bind` places
one declared slot on a partition of this machine that carries the slot's role. It makes one folder at the root
of the partition, `isle-slots/<app>/<slot>`, owned as the slot says, and writes the binding into this machine's
storage record beside the partition's roles. A folder that is there already is kept with what is in it and with
its owner, so a disk carried from another machine is taken up as it is. It refuses, and changes nothing: a slot
the app does not declare; a partition that is away, does not carry the role or needs attention in
`partition check`; a slot that is written on a partition mounted read-only; less free space than `sizeBytes`;
something at the folder's name that is not a folder of the partition itself (a link is never followed); and a
slot that is bound to another partition already. `slot unbind` takes the binding out of the record and leaves
the folder and its data where they are. No verb moves a slot's data between partitions yet, and nothing keeps a
second copy yet: `copies: 2` is recorded and said, no more.

`slot list` shows every slot an app of this machine declares and every slot this machine has bound, with its
state: `ok`, `not bound`, `not declared`, `declaration cannot be read`, `partition away`,
`partition needs attention`, `role taken away`, `declared with another role`, `folder missing` or
`mounted read-only`. `slot check` shows the same and exits 1 unless every slot (of one app, with `--app`) is
`ok`. `partition retire` refuses to take a role away while a slot is bound under it.

An app that declares slots is started only with them. `isle store publish` refuses a release whose `storageSlots`
cannot be read, and `isle store install` refuses one that names a service its compose file lacks. An install
keeps what the release declares (as `slot declare` does) and then, before anything is deployed, refuses unless
every slot is `ok`. On the first install of such an app that is a refusal: the list is kept, so type
`sudo isle storage slot bind <app> <slot> --partition <filesystem-identifier>` for each slot and install again.
No partition is chosen for a slot by the CLI. `isle app deploy` runs `slot check --app <app>` before it writes or
starts anything, and the app's overlay then gives each slot's folder to its service at `target`, read-only unless
the slot is `writable`; Docker is told never to make a missing folder. Two folders at one place in one service
are refused. `isle reconcile` does not restart an app whose slots are not all `ok`, or whose overlay does not give
exactly the folders that are bound now: deploy or install it again. An app without `storage-slots.json` is
started as before. Not built: a unit that stops the app when its partition goes away and starts it when it
returns; until then a partition that is unmounted under a running app leaves the app running on a folder that
is gone; `partition unmount` names the slots bound to the partition and asks for `--with-datasets`.

These records describe one machine, and each machine keeps its own. With `--isle`, `role list` and `role show`
print what every machine of the isle reported, side by side, from any machine: each machine adds its partitions
that carry a role (role, filesystem identifier, class, size, free space, state, datasets; no hardware serial) to
the device report it already sends to the core, and the view reads the core's device list. A partition with
several roles is reported once per role, so the report has the same shape as before. It shows when each
machine reported, and names the machines that sent no storage report (an older isle CLI) or one this version
cannot read. It is a report, not a check: whether a partition is really there is asked on the machine itself with
`partition check`. Polari reads the same device list; there is no second store.

The roles are not yet part of the core's placement record, and no verb reads a role to decide where data goes.
Not built: adding a partition to a partition table that is there.
Tests: `python3 isle-cli/scripts/test-storage-roles.py`, `test-storage-retire.py`, `test-storage-health.py`, `test-storage-create.py`,
`test-storage-mounting.py`, `test-storage-kinds.py`, `test-storage-slots.py`, `test-storage-slot-start.py`, `test-storage-isle.py` and `test-storage-devices.py` (fixtures; `systemctl` is never started by them; `test-storage-create.py` also runs the real `sfdisk`, `blkid` and
`mkfs.ext4` on image files it makes in a temporary folder, and never on a device).

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
