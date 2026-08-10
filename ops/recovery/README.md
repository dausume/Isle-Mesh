# ops/recovery — reusable isle-core recovery + verification tools

Reserved from root-level helpers (originally written for the
2026-07-03 power-surge recovery, but generally useful):

- **fix-live-certs.sh** — regenerate any empty/invalid isle app
  SSL cert+key (0-byte PEMs from a crash/power-loss make the
  vlan-agent crash-loop; nothing serves on 443). Self-signed,
  SAN `<base>.local` + `<base>.isle`, restarts the agent.
  `sudo bash ops/recovery/fix-live-certs.sh`. Candidate to promote
  to an `isle certs repair` verb (no such verb exists today).
- **verify-teardown.sh** — read-only audit that
  `isle destroy --purge --force` removed every artifact the
  teardown-completeness work flagged (systemd units, udev rules,
  helper bins, NM profiles, /etc + /var trees, docker nets +
  containers, router VM) while KEEPING the CLI.
  `bash ops/recovery/verify-teardown.sh`.

RETIRED (one-time, not carried forward):
- `fix-live-registry.sh` — deployed the hardened registry relay to
  a running box that predated the fix; the relay is now hardened
  in-tree (isle-agent/isle-host-agent/isle-host-agent-relay.sh),
  so the migration is obsolete.
- `RUN-app-install-test.md` — a dated uninstall→reinstall runbook
  referencing the superseded isle-manager-app deb + dev-consolidation.
