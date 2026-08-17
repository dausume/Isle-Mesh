# polari-isle — the polari SUB-PROJECT of the isle

This directory is the versioned home of the isle's polari deployment
(prf-isle, the lean tier): compose + runtime config + the self-feed
pusher. It exists so that EVERYTHING lives inside the suite — cloning
polari-suite (which nests Isle-Mesh, which nests this) carries the whole
polari-on-isle deployment; nothing is hand-made on a device anymore.

`isle-polari-deploy` (isle-cli/scripts) SEEDS `~/polari-isle` from this
directory on first run — the deployed copy is the working instance
(its runtime-config may be rebased per device: `isle polari instance
rebase`); this directory is the source of truth for NEW deployments.

Files:
- `docker-compose.yml`     prf-isle backend+frontend, agent-only ingress
                           (no published ports), sqlite, no Keycloak —
                           the lean tier by design
- `runtime-config.json`    frontend runtime config (polari.isle names)
- `push-to-polari.sh`      the isle feeds ITSELF into its polari every
                           2 min (device facts, registry, fragments,
                           engines, DNS reconcile, net ledger). Device
                           identity = /etc/isle-mesh/canonical-name,
                           hostname fallback.

Deploy/teardown live with the other verbs: isle-cli/scripts/
isle-polari-deploy.sh / isle-polari-teardown.sh.

NO security material lives here (deploy-time input rule — see
`isle security setup`); the lean tier carries no credentials at all.
