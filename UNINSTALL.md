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

## What is deliberately left

- Data volumes on plain `remove` (that's the point of remove).
- Shared dependencies (docker, libvirt) and group memberships.
- Anything the final report lists under "left in place" — each line
  comes with the command to revert it manually.
