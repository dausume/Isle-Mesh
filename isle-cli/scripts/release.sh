#!/bin/bash
# release.sh - isle release: one reviewed release across the isle (slices 1-2).
#   isle release build <out-dir> --previous <dir>         no sudo: the packet of the next CLI release, built from one
#                                         commit of the isle repository (release_build.py); nothing is published
#   isle release build <out-dir> --dependencies <set dir> no sudo: a packet that holds one dependency set and nothing else
#   isle release check <packet-dir> [--expect <run-id>]   read-only validation and summary
#   sudo isle release run <packet-dir> [--expect <run-id>] [--wait <min>|--no-wait]
#                                         core: publish, wait until served, own steps, sign the approval, announce,
#                                         then wait for the approved members to report (exit 0 / 1 failed / 3 pending)
#   sudo isle release follow [--auto]     member: verify the core's signed approval against the isle root, then run this
#                                         device's steps. --auto (isle-release-follow.timer) prompts for nothing and is
#                                         quiet when there is nothing to do
#   isle release status [run-id]          per-step receipts (on the core also the member reports)
#   sudo isle release approval-key        core: mint the approval key if needed and show its certificate
#   isle release setting [core-only|any-device]   where a run may be started (changed on the core, with sudo; default
#                                         core-only)
#   isle release offer                    member: the packet this device offers to the core, and the core's answer
#   sudo isle release offer withdraw      member: take the offer down
#   isle release intake                   any-device: whether the core takes members' packets, and what it answered
#   sudo isle release intake run [--auto] core: fetch one offered packet and run it now (isle-release-intake.timer does this)
# With any-device, `sudo isle release run <packet-dir>` typed on a member offers the packet there (release_offer.py);
# the core fetches it from that member and runs it (release_intake.py).
# Packet kinds: publish dependencies (apt-repo publish --only-dependencies), runtime (update publish-runtime), debs (apt-repo
# publish --from), catalog (store publish), in that fixed order, from a root-owned copy of the packet; update-apply may name "published". Steps: update-apply, store-install, module-add,
# hotfix {name} (isle hotfix apply <name> --yes on that device; the device's own CLI decides whether it knows the name).
set -u
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "${1:-help}" in
    # run / follow / approval-key / setting <value> need root, but release_run.py says so itself, AFTER it has checked that this device has
    # the role the verb needs: a member must hear "core only" without first being asked for a password.
    build|run|follow|approval-key|setting|intake|offer|check|status|help|-h|--help) ;;
    *) echo "unknown release command: $1 (try: isle release help)"; exit 2 ;;
esac
exec python3 "$DIR/release_run.py" "$@"
