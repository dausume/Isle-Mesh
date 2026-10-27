#!/bin/bash
# App dependency transactions: local Isle supply first, Ubuntu fallback online
# only. Does not rewrite the host's apt sources or change network settings.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run this package installation through the app or sudo.' >&2; exit 1; }
[ "$#" -gt 0 ] || { echo 'usage: apt-install.sh <package[=version]|absolute.deb> ...' >&2; exit 1; }
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MODE=$(cat /etc/polari/install-mode 2>/dev/null || echo online)
[[ "$MODE" = online || "$MODE" = offline ]] || { echo 'Unknown installation mode; refusing package installation.' >&2; exit 1; }
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
CACHE=/var/cache/isle-mesh/archives
REPO=/etc/isle-mesh/apt/repo
CA=/etc/isle-mesh/ca/isle-root.crt
KEY=/usr/share/keyrings/isle-archive-keyring.gpg
mkdir -p "$WORK/lists/partial" "$WORK/sourceparts" "$WORK/log" "$WORK/cache" "$WORK/local-debs" "$CACHE/partial"
chmod 755 "$WORK" "$WORK/lists" "$WORK/lists/partial" "$CACHE" "$CACHE/partial"
: > "$WORK/sources.list"
# File-path DEBs must also be indexed through copy: before --no-download;
# apt's direct-path method otherwise skips materializing its archive cache.
TARGETS=()
for target in "$@"; do
    if [[ "$target" = /*.deb ]]; then
        package=$(dpkg-deb -f "$target" Package)
        version=$(dpkg-deb -f "$target" Version)
        filename=${target##*/}
        ln -s "$target" "$WORK/local-debs/$filename"
        dpkg-deb -f "$target" >> "$WORK/local-debs/Packages"
        digest=$(sha256sum "$target"); digest=${digest%% *}
        printf 'Filename: ./%s\nSize: %s\nSHA256: %s\n\n' "$filename" "$(stat -c %s "$target")" "$digest" >> "$WORK/local-debs/Packages"
        TARGETS+=("$package=$version")
    else
        [[ "$target" != -* ]] || { echo 'Package options are not accepted as targets.' >&2; exit 1; }
        TARGETS+=("$target")
    fi
done
if [ -s "$WORK/local-debs/Packages" ]; then
    printf 'deb [trusted=yes] copy:%s/local-debs ./\n' "$WORK" >> "$WORK/sources.list"
fi
cat > "$WORK/apt.conf" <<'APTCONF'
Dir::Etc::main "/dev/null";
Dir::Etc::parts "/dev/null";
Acquire::Retries "0";
Acquire::http::Timeout "8";
Acquire::https::Timeout "8";
APTCONF
[ ! -f "$CA" ] || printf 'Acquire::https::apt.isle::CaInfo "%s";\n' "$CA" >> "$WORK/apt.conf"
OPTS=(-o "Dir::Etc::SourceList=$WORK/sources.list" -o "Dir::Etc::SourceParts=$WORK/sourceparts"
      -o "Dir::State::lists=$WORK/lists" -o "Dir::Cache::archives=$CACHE"
      -o "Dir::Cache=$WORK/cache" -o "Dir::Log=$WORK/log"
      -o "Dir::State::extended_states=$WORK/extended_states" -o APT::Get::List-Cleanup=0)
OPTS+=(-o APT::Update::Error-Mode=any)
export APT_CONFIG="$WORK/apt.conf"
# Resolve using installed packages and local sources FIRST. Merely enabling
# online mode must not contact upstream when the local pool is sufficient.
# No >1000 priority pins, forced downgrades or reinstalls are used.
try_local_transaction() {
    apt-get "${OPTS[@]}" --simulate --no-remove install --no-install-recommends "${TARGETS[@]}" > "$WORK/plan.log" 2>&1 || return 1
    apt-get "${OPTS[@]}" --download-only --no-remove install -y --no-install-recommends "${TARGETS[@]}" > "$WORK/stage.log" 2>&1 || return 1
    cat "$WORK/plan.log" "$WORK/stage.log"
}
try_source() {
    local source="$1"
    cp "$WORK/sources.list" "$WORK/previous.list"
    printf '%s\n' "$source" >> "$WORK/sources.list"
    if ! apt-get "${OPTS[@]}" update > "$WORK/update.log" 2>&1; then
        cp "$WORK/previous.list" "$WORK/sources.list"
        # A trust/hash failure is not an ordinary cache miss. Never mask it
        # with another repository; ask for repair of the authenticated supply.
        if grep -Eqi 'NO_PUBKEY|BADSIG|EXPKEYSIG|Hash Sum mismatch|not signed|certificate verification failed|certificate is NOT trusted' "$WORK/update.log"; then
            cat "$WORK/update.log" >&2; exit 2
        fi
        echo 'Local package source unavailable; checking other permitted sources.' >&2
        return 1
    fi
    cat "$WORK/update.log"
    try_local_transaction
}
BEFORE=$(find "$CACHE" -maxdepth 1 -name '*.deb' -printf '%f %s %T@\n' | sort)
READY=0
if apt-get "${OPTS[@]}" update > "$WORK/update.log" 2>&1 && try_local_transaction; then READY=1; fi
if [ "$READY" = 0 ]; then
    if [ -f "$REPO/InRelease" ] && [ -f "$REPO/isle-archive-keyring.gpg" ]; then
        if try_source "deb [signed-by=$REPO/isle-archive-keyring.gpg] copy:$REPO ./"; then READY=1; fi
    elif [ -f "$CA" ] && [ -f "$KEY" ]; then
        if try_source "deb [signed-by=$KEY] https://apt.isle ./"; then READY=1; fi
    fi
fi
MEDIUM=$(cat /etc/polari/offline-source 2>/dev/null || true)
if [ "$READY" = 0 ] && [ -n "$MEDIUM" ] && [ -f "$MEDIUM/ISLE_OFFLINE_BUNDLE" ]; then
    # Operational installers never execute a verifier from mutable user media.
    # The DEB stages its medium root-owned below /usr/share. Removable/user-
    # writable media must first be staged by that installer, not reused as root
    # code later after its original approval.
    python3 - "$MEDIUM" <<'PYTRUST'
import os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1])
for path in [root,*root.parents,*root.rglob('*')]:
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        sys.exit('Untrusted mutable medium: stage it using the offline installer first')
PYTRUST
    bash "$MEDIUM/scripts/verify-offline.sh" "$MEDIUM"
    if try_source "deb [trusted=yes] copy:$MEDIUM/apt ./"; then READY=1; fi
fi
if [ "$READY" = 0 ]; then
    if [ "$MODE" = offline ]; then
        cat "$WORK/plan.log" >&2
        echo 'Offline: required package/version unavailable from installed packages and local Isle supply; no internet fallback.' >&2
        exit 2
    fi
    echo 'Required package/version not available locally; checking configured online repositories.'
    # Host repositories are a fallback, never queried for a complete local
    # transaction. This includes trusted, administrator-configured vendor repos.
    [ ! -f /etc/apt/sources.list ] || cp /etc/apt/sources.list "$WORK/sourceparts/50-host-main.list"
    for source in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -f "$source" ] || continue
        case "${source##*/}" in *isle*) continue ;; esac
        cp "$source" "$WORK/sourceparts/60-${source##*/}"
    done
    # Upstream remains behind available local sources for equal versions.
    apt-get "${OPTS[@]}" update
    apt-get "${OPTS[@]}" --simulate --no-remove install --no-install-recommends "${TARGETS[@]}"
    apt-get "${OPTS[@]}" --download-only --no-remove install -y --no-install-recommends "${TARGETS[@]}"
fi
DEBIAN_FRONTEND=noninteractive apt-get "${OPTS[@]}" --no-download --no-remove install -y --no-install-recommends "${TARGETS[@]}"
AFTER=$(find "$CACHE" -maxdepth 1 -name '*.deb' -printf '%f %s %T@\n' | sort)
# On a core, retain newly acquired dependencies in the signed shared repository.
# A member keeps its own archive cache; it never becomes an archive authority.
if [ "$BEFORE" != "$AFTER" ] && [ -d /etc/isle-mesh/router ] && [ -f "$REPO/InRelease" ]; then
    if ! bash "$SCRIPT_DIR/apt-repo.sh" publish --from "$CACHE" > "$WORK/publish.log" 2>&1; then
        echo 'Installed successfully, but shared dependency cache publication needs attention:' >&2
        tail -n 8 "$WORK/publish.log" >&2
    fi
fi
