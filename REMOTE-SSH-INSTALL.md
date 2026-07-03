# Remote CLI-Only Install over SSH

How to install the `isle` CLI on a remote device using only SSH — no desktop
app, no GUI on the target. Proven end-to-end 2026-07-03 (isle-core → N95
mini-PC, Ubuntu 22.04, stock: no git, no node, no docker).

## Who this is for

The **advised route for most people is the USB install** flashed by the
app/CLI — the app should steer users there first. Use the SSH route for
devices where USB isn't practical (headless boxes, boards with no free port,
remote machines). The long-term goal is that the **app wraps every step
below** so the user never types `ssh` themselves; this doc is the reference
for that wrapper (`isle remote install <user@host>`, future) and for anyone
doing it by hand today.

## Assumptions

The target device has SSH **already installed and primed** (an account you
know, sshd running, reachable on the LAN). If not, on the target run:

```sh
sudo apt-get install -y openssh-server && sudo systemctl enable --now ssh
```

then find its address with `hostname -I` (or use `<hostname>.local` via mDNS).
`mesh-ssh-SERVER-setup.sh` automates this plus key authorization and hardening.

## The flow (from the controller machine)

1. **Authorize your key** (one password prompt, then never again):

   ```sh
   ssh-copy-id <user>@<target>
   ```

2. **Copy the checkout** (target needs no git):

   ```sh
   rsync -a ~/Isle-Mesh/ <user>@<target>:~/Isle-Mesh/
   ```

3. **User-space node** (target needs no root; skip if node ≥ 18 exists):

   ```sh
   ssh <user>@<target> '
     mkdir -p ~/.local/opt ~/.local/bin && cd ~/.local/opt &&
     N=node-v22.17.0-linux-x64 &&
     wget -q https://nodejs.org/dist/v22.17.0/$N.tar.xz &&
     tar xf $N.tar.xz && rm $N.tar.xz &&
     ln -sf ~/.local/opt/$N/bin/node ~/.local/bin/node'
   ```

4. **Install the CLI** — user-space link, no sudo needed:

   ```sh
   ssh <user>@<target> \
     'ISLE_CLI_LINK=$HOME/.local/bin/isle bash ~/Isle-Mesh/cliOnlyInstall.sh'
   ```

5. **Verify**:

   ```sh
   ssh <user>@<target> 'PATH=$HOME/.local/bin:$PATH isle status'
   ```

   Expect "No router detected" on a fresh device — that's success; the CLI
   runs and correctly sees an empty mesh.

## What still needs sudo (later, on the target)

Joining a mesh for real (`isle router init`, docker, host DNS/mDNS changes)
mutates the system and needs root. Over headless SSH, `sudo` cannot prompt —
the wrapper flow must either walk the user through entering their password on
the device once, or pre-stage a scoped sudoers entry. `cli-paths.sh` only
elevates when the touched path actually requires it (`_isle_priv`), so pure
CLI installs stay sudo-free.
