# Uninstalling isle-mesh (and the polari it carries)

One teardown engine, three doors. **The terminal way is the
implementation; the desktop and in-app routes run the SAME shell steps
— the UI only adds polkit for privilege.** Whatever door you use, the
same conditionals handle every device shape (core with a router VM,
remote whose wifi was taken over by the join, plain member).

## The three routes

| route | what you do | what actually runs |
|---|---|---|
| **Terminal** | `sudo isle uninstall --everything` (or `apt remove` / `apt purge isle-mesh-cli`) | the engine scripts directly |
| **Desktop** | software-center / right-click uninstall | apt → the deb's prerm/postrm → the same engine scripts |
| **In-app** (store shell) | the Uninstall buttons | the same `isle uninstall` verbs via **polkit** — no logic of its own |

## Remove vs purge (the data policy)

- **`apt remove isle-mesh-cli`** — stops EVERYTHING (apps, agent,
  router VM, timers) and hands networking back to the OS, but
  **preserves data**: `/etc/isle-mesh` (your CA!) and the docker
  volumes survive. Reinstalling picks the isle back up.
- **`apt purge isle-mesh-cli`** — additionally erases config/state.
  Volumes are **backed up first** to
  `/var/backups/isle-mesh-purge-<date>/`, then deleted.
- **`sudo isle uninstall --everything`** — the full wipe as one honest
  interactive verb: backup → `destroy --purge` → network handback →
  volumes (backup-then-delete) → apt purge of every isle package →
  `--verify` sweep.
- **`sudo isle uninstall --verify`** — proves the zero state:
  containers, volumes, images, router VM, packages, directories all
  gone, and names which service owns your network.

## The store's buttons (unin-4) — the UI runs the same steps via polkit

Each button in the store shell is ONE pkexec invocation of the verb
printed beside it — the UI holds zero teardown logic, and the page
shows the terminal equivalent next to every button:

| button (app detail) | exactly what runs |
|---|---|
| **Uninstall** | `pkexec isle store uninstall <name> --yes` — this device's copy only; **data preserved** (volumes + `/etc/isle-mesh/apps/<name>` survive for a reinstall) |
| **Uninstall + erase data** | `pkexec isle store uninstall <name> --yes --purge` — volumes **backed up** to `/var/backups/isle-mesh-app-<name>-<date>/` first, then deleted; app config + `<name>.isle` DNS dropped |
| **Remove isle-mesh…** (page footer) | opens a terminal running `pkexec isle uninstall --everything` — the verb is interactive, so its yes/no and the core-cascade typed confirmation happen in that terminal; a stray click removes nothing |

`isle store uninstall` acts on THIS device only: the mesh-app
deployment here and/or the launcher deb here (it says so honestly
when neither exists). Other devices remove their own copies.

## What the engine handles per device shape (conditionals, not
separate scripts)

- **Core**: router VM + bridges, every deployed app container
  (exact-name families only), exposure doors, apt-on-mesh, registry,
  self-feed/trust timers, hosts pins, split-DNS, hardening.
- **Remote that went through the join takeover**: wifi ownership is
  handed BACK to NetworkManager (wpa_supplicant@ instances +
  systemd-networkd disabled **only when NetworkManager is active to
  take over** — a box is never left without a network owner), stale
  isle addresses and dead router routes flushed, `~isle` split-DNS
  stripped from NM profiles.
- **Everywhere**: third-party systems (e.g. odoo) and development code
  checkouts are NEVER touched; backups are excluded from every sweep;
  every step tolerates failure so the package manager can never wedge.

## Uninstalling a CORE — the cascade (there is no going back)

Deleting an isle's CORE ends the isle for EVERY member: the CA,
router DNS, apt-on-mesh, and the core polari die with it, and a new
core-install creates a DIFFERENT isle (new CA — members must re-join).

- `isle uninstall --everything` on a core shows the full warning and
  requires typing **`delete the isle`** — even `--force` doesn't skip
  it (`ISLE_CONFIRM_DELETE=yes` exists for automation). Plain
  `apt remove` on a core cannot prompt (Debian rule), so the deb path
  proceeds — the interactive warning lives in the verb.
- Software on OTHER devices cannot be uninstalled remotely. Instead
  the dying core sends a last-gasp **ISLE-ENDING kill signal**
  (fingerprint-tagged UDP broadcast on the isle subnet). Every member
  runs **isle-watch** (enabled at join/onboard; `isle watch status`):
  - on a matching signal it does the SAFE stage automatically — stops
    isle app containers (nothing deleted) and records the event;
  - members offline at that moment catch up via the poll fallback
    (core unreachable for ~an hour records a softer event);
  - the next time a human opens the store on that device, they are
    asked: remove all polari-isle apps here? **Removal is never
    automatic** — it needs that human, and the prompt restates that
    there is no going back (volumes are backed up first). "The isle
    is back" clears a false alarm (`isle watch clear`).
- Honesty note: the signal is fingerprint-tagged, not signed — a
  device already inside the isle could spoof it. The blast radius is
  bounded on purpose: a spoof can only STOP containers (recoverable);
  it can never delete anything.

Modules and engines ride the same sweeps: `polari-module-*` debs join
the purge family, module/engine containers are covered by the
exact-name container families, and `--verify` counts them all.

## What is deliberately left

- Data volumes on plain `remove` (that's the point of remove).
- Shared dependencies (docker, libvirt) and group memberships.
- Anything the final report lists under "left in place" — each line
  comes with the command to revert it manually.
