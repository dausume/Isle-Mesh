#!/usr/bin/env bash
# isle storage <verb> ... — one entry point, three lanes (contract §2.1):
#   journaled operations (root, through operations.py so the dataset lock and journal exist before
#     any side effect): migrate, sync on the hosting device; authorize, mark-migrated, cutover,
#     rollback, recover, accept, reconcile-address, witness on the device the verb names;
#   publisher-side root CLIs under the publication lock, not operations: publish-manifest, gc;
#   core evidence relay (not journaled): relay;  placement view for the backend: view-export [--post];
#   read-only verbs for anyone: list, check, check-path, guard-domain, status, domain-entry, fence-check,
#     placement (prints the record), device list, partition list|check, role list|show, units check;
#   this machine's storage records (root, under the records' own lock; no dataset, so not journaled):
#     device register, partition assign;
#   units apply --dataset D: root, journaled (rewrites the dataset's systemd units on the hosting device).
#   (reconcile.sh runs storage_units.py after-update itself; it is not a verb here.)
set -euo pipefail
here="$(dirname "${BASH_SOURCE[0]}")"
verb="${1:-}"
case "$verb" in
    migrate|sync|authorize|mark-migrated|cutover|rollback|recover|accept|reconcile-address|witness|units-apply)
        if [ -z "${ISLE_OPERATION_ID:-}" ]; then
            exec python3 "$here/operations.py" run storage "$@"
        fi
        case "$verb" in
            migrate) exec python3 "$here/storage_migrate.py" "$@" ;;
            witness) exec python3 "$here/storage_witness.py" "$@" ;;
            units-apply) shift; exec python3 "$here/storage_units.py" apply "$@" ;;
            sync) exec python3 "$here/storage_sync.py" "$@" ;;
            *) exec python3 "$here/storage_core.py" "$@" ;;
        esac ;;
    relay)  # core, root; evidence only (appends local inbound receipts/witnesses, then mark-migrated), so not journaled itself
        exec python3 "$here/storage_core.py" "$@" ;;
    publish-manifest|gc|domain-entry|fence-check|after-publication|view-export)
        exec python3 "$here/storage_placement.py" "$@" ;;
    placement)
        shift; exec python3 "$here/storage_placement.py" status "$@" ;;
    units)  # check: read-only for anyone.  apply: root, journaled under the dataset lock as 'units-apply'
        if [ "${2:-}" = apply ]; then
            shift 2; exec python3 "$here/operations.py" run storage units-apply "$@"
        fi
        [ "${2:-}" = check ] || { echo "Usage: isle storage units check [--dataset D] | apply --dataset D [--overrides keep|remove]" >&2; exit 1; }
        shift; exec python3 "$here/storage_units.py" "$@" ;;
    device|partition|role)  # what this machine has, by serial and by filesystem identifier, and the role of each partition
        exec python3 "$here/storage_roles.py" "$@" ;;
    authorize-placement|accept-placement)
        echo "storage: '$verb' is now the journaled operation 'isle storage ${verb%-placement} ...'" >&2; exit 1 ;;
    *)
        exec python3 "$here/storage_cli.py" "$@" ;;
esac
