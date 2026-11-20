#!/bin/bash
# apt-repo.sh — `isle apt-repo`: the APT-ON-MESH repo (handoff
# ISLE_ONBOARDING §5b distribution). The registry (§28) is the
# docker-image half of offline supply; this is the DEB half: a
# SIGNED flat apt repo served at https://apt.isle so a remote pulls
# isle-mesh-cli / polari-shell-core / isle-app-store over the isle,
# no internet needed.
#
#   isle apt-repo publish [--from <dir>] [--dependencies-from <dir>]
#                                      (core) collect platform + dependency debs,
#                                          sign, (re)serve at apt.isle
#   isle apt-repo enable                   (client) trust the repo key +
#                                          add the apt source here
#   isle apt-repo pin [check|apply]        (client) the apt pin: only isle-* and
#                                          polari-* packages come from this source
#   isle apt-repo status                   what the repo serves
#
# Two indexes over one set of package files (apt separation, CLI release 89):
#   ./                 isle-* and polari-* packages only. This is what a device's apt reads.
#   deps/<release>/    every other package: the operating system packages of the offline
#                      dependency set, built for the Ubuntu release of this core (noble, ...).
#                      Only `isle apt install` reads it, and only on a device of that release.
#
# Trust model: the archive-signing KEY is fetched over a TLS channel
# the isle root CA authenticates (tier-1 trust must already be
# done), then apt verifies every index against that key (signed-by).
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; N="\033[0m"
ok(){ echo -e "${G}[ OK ]${N} $*"; }
warn(){ echo -e "${Y}[WARN]${N} $*"; }
die(){ echo -e "${R}[FAIL]${N} $*"; exit 1; }

APT_DIR=/etc/isle-mesh/apt
REPO="$APT_DIR/repo"
GNUPG="$APT_DIR/gnupg"
KEYID="Isle Mesh Apt <apt@isle>"
KEYRING_NAME=isle-archive-keyring.gpg
URL=https://apt.isle
CA=/etc/isle-mesh/ca/isle-root.crt
CURL="curl -sf --max-time 8 --cacert $CA"
SOURCE_LIST=/etc/apt/sources.list.d/00-isle-mesh.list
PIN_SRC="$SCRIPT_DIR/../apt/isle-mesh.pref"
PIN_DST=/etc/apt/preferences.d/isle-mesh.pref

# F1: every file written into $REPO gets a NEW inode: stage as "$(part_of "$dest")", sync, mv -f over the
# destination. An in-place writer would otherwise corrupt a hardlinked snapshot object.
part_of() { # $1 dest -> hidden staging sibling (a leading dot keeps it out of every content inventory)
    printf '%s/.%s.part-%s' "$(dirname -- "$1")" "$(basename -- "$1")" "$$"
}
commit_part() { # $1 dest: publish the staged "$(part_of "$1")"
    # sync + mv without a directory fsync can lose the rename on a crash; acceptable because the previous file survives intact
    sudo sync -- "$(part_of "$1")" 2>/dev/null || sudo sync
    sudo mv -f -- "$(part_of "$1")" "$1"
}
write_atomic_from_stdin() { # $1 dest
    if ! sudo tee -- "$(part_of "$1")" >/dev/null; then sudo rm -f -- "$(part_of "$1")"; return 1; fi
    commit_part "$1" || { sudo rm -f -- "$(part_of "$1")"; return 1; }
}
copy_atomic() { # $1 src, $2 dest
    if ! sudo cp -- "$1" "$(part_of "$2")"; then sudo rm -f -- "$(part_of "$2")"; return 1; fi
    commit_part "$2" || { sudo rm -f -- "$(part_of "$2")"; return 1; }
}

os_codename() { # the Ubuntu release name of this device (noble, jammy, ...); prints nothing when it cannot be read
    ( . /etc/os-release 2>/dev/null; n="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"; [[ "$n" =~ ^[a-z][a-z0-9]*$ ]] && printf '%s' "$n" )
}

INDEX_ERROR=""
build_index() { # $1 private dir holding Packages, $2 Origin, $3 Label, $4 Suite, $5 Codename ('' = none), $6 Description
    local dir="$1" fields=(-o "APT::FTPArchive::Release::Origin=$2" -o "APT::FTPArchive::Release::Label=$3"
        -o "APT::FTPArchive::Release::Suite=$4" -o APT::FTPArchive::Release::Acquire-By-Hash=yes
        -o "APT::FTPArchive::Release::Description=$6")
    [ -z "$5" ] || fields+=(-o "APT::FTPArchive::Release::Codename=$5")
    INDEX_ERROR="index build failed"
    sudo gzip -kf "$dir/Packages" || return 1
    ( cd "$dir" && sudo sh -c 'apt-ftparchive "$@" release . > Release' sh "${fields[@]}" ) || return 1
    INDEX_ERROR="index builder did not enable Acquire-By-Hash"
    grep -qx 'Acquire-By-Hash: yes' "$dir/Release" || return 1
    INDEX_ERROR="Release signing failed"
    sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes -abs -o "$dir/Release.gpg" "$dir/Release" || return 1
    INDEX_ERROR="InRelease signing failed"
    sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes --clearsign -o "$dir/InRelease" "$dir/Release" || return 1
}

# Publish immutable hash-addressed indexes BEFORE changing InRelease.
# Previous hashes are retained, so a client holding the previous signed
# release can still finish while a newer generation becomes current.
commit_index() { # $1 private stage (Packages, Packages.gz, Release, Release.gpg, InRelease), $2 the served directory
    sudo python3 - "$1" "$2" <<'PYPUBLISH'
import hashlib,os,pathlib,shutil,sys,tempfile
stage,repo=map(pathlib.Path,sys.argv[1:])
created=[p for p in (repo.parent,repo) if not p.exists()]
repo.mkdir(parents=True,exist_ok=True)
for p in created:os.chmod(p,0o755)
def digest(path,algorithm='SHA256'):
    result=hashlib.new(algorithm.lower())
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):result.update(block)
    return result.hexdigest()
def prepared(source,directory):
    fd,name=tempfile.mkstemp(prefix='.publish-',dir=directory)
    os.close(fd)
    try:
        shutil.copyfile(source,name);os.chmod(name,0o644)
        fd=os.open(name,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except BaseException:os.unlink(name);raise
    return pathlib.Path(name)
# apt-ftparchive may advertise SHA512 as well; clients can choose the
# strongest advertised algorithm, so provide both hash namespaces.
for algorithm in ('SHA256','SHA512'):
    hashdir=repo/'by-hash'/algorithm;hashdir.mkdir(parents=True,exist_ok=True)
    os.chmod(repo/'by-hash',0o755);os.chmod(hashdir,0o755)
    for name in ('Packages','Packages.gz'):
        source=stage/name;checksum=digest(source,algorithm);destination=hashdir/checksum
        temporary=prepared(source,hashdir)
        try:
            try:os.link(temporary,destination)
            except FileExistsError:
                if destination.is_symlink() or not destination.is_file() or digest(destination,algorithm)!=checksum:
                    raise ValueError('invalid existing immutable by-hash index: '+checksum)
        finally:temporary.unlink(missing_ok=True)
# Each canonical filename is replaced on the destination filesystem. The
# single signed InRelease is the commit point and always appears last.
for name in ('Packages','Packages.gz','Release.gpg','Release','InRelease'):
    temporary=prepared(stage/name,repo)
    try:os.replace(temporary,repo/name)
    finally:temporary.unlink(missing_ok=True)
PYPUBLISH
}

clean_stale_staging() { # caller holds the publication lock: no live publisher owns a staging file in $REPO
    local f pid
    while IFS= read -r -d '' f; do
        pid="${f##*.part-}"
        if [[ "$pid" =~ ^[0-9]+$ ]] && ! kill -0 "$pid" 2>/dev/null; then
            sudo rm -f -- "$f" && warn "removed stale staging file $f"
        fi
    done < <(sudo find "$REPO" -maxdepth 1 -type f -name '.*.part-[0-9]*' -print0 2>/dev/null)
    while IFS= read -r -d '' f; do
        sudo rm -f -- "$f" && warn "removed stale publication leftover $f"
    done < <(sudo find "$REPO" -type f -name '.publish-*' -not -path "$REPO/snapshot/*" -print0 2>/dev/null)
}

ensure_key() {
    sudo mkdir -p "$GNUPG"; sudo chmod 700 "$GNUPG"
    if ! sudo GNUPGHOME="$GNUPG" gpg --batch --list-secret-keys 2>/dev/null | grep -q "apt@isle"; then
        warn "generating the archive signing key (once)"
        sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --quick-gen-key "$KEYID" ed25519 sign 0 \
            || die "key generation failed"
    fi
    ok "archive signing key present"
}

publish() {
    # deb sources, first hit wins: --from, the hand-staged dir (the
    # INVOKING user's home — under sudo $HOME is /root, finding #6),
    # then the from-code build output (.generated/debs of a suite
    # checkout) — the normal fresh-box world after build-polari-isle-deb
    local UH; UH="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)"
    local FROM="" DEPENDENCIES_FROM=""
    while [ $# -gt 0 ]; do case "$1" in
        --from) [ $# -ge 2 ] || die "--from needs a directory"; FROM="$2"; shift 2 ;;
        --dependencies-from) [ $# -ge 2 ] || die "--dependencies-from needs a directory"; DEPENDENCIES_FROM="$2"; shift 2 ;;
        *) die "unknown publish argument: $1" ;;
    esac; done
    if [ -z "$FROM" ]; then
        for c in "$UH/polari-shells" "$UH/polari-suite/.generated/debs" \
                 "$HOME/polari-shells"; do
            ls "$c"/*.deb >/dev/null 2>&1 && { FROM="$c"; break; }
        done
    fi
    [ -n "$FROM" ] && [ -d "$FROM" ] || die "no deb source dir found (looked: ~/polari-shells, ~/polari-suite/.generated/debs; or pass --from <dir>)"
    # The operating system packages in this repository are indexed under this core's Ubuntu release name.
    local CODENAME; CODENAME=$(os_codename || true)
    [ -n "$CODENAME" ] || die "cannot read this core's Ubuntu release name from /etc/os-release; repository unchanged"
    [ -f "$SCRIPT_DIR/apt_index_split.py" ] || die "this CLI is incomplete (apt_index_split.py is missing); repository unchanged"
    # Serialise automatic dependency publications and interactive publishes.
    # Readers never need this lock: committed InRelease + by-hash is immutable.
    command -v flock >/dev/null 2>&1 || die "flock is required for repository publication"
    sudo mkdir -p "$APT_DIR" || die "cannot create apt state directory"
    sudo touch "$APT_DIR/.publish.lock" && sudo chmod 644 "$APT_DIR/.publish.lock" \
        || die "cannot prepare publication lock"
    local PUBLISH_LOCK_FD
    exec {PUBLISH_LOCK_FD}<"$APT_DIR/.publish.lock" || die "cannot open publication lock"
    flock "$PUBLISH_LOCK_FD" || die "cannot lock repository publication"
    clean_stale_staging

    # Retain all versions and architectures, including old versions still
    # required by an installed platform release. Filenames/sort -V are not a
    # Debian-version policy. Validate every input before changing the repo.
    local COPY_PLAN
    COPY_PLAN=$(mktemp) || die "cannot create publication plan"
    if ! python3 - "$FROM" "$DEPENDENCIES_FROM" "$REPO" > "$COPY_PLAN" <<'PYPACKAGES'
import hashlib,pathlib,re,subprocess,sys
platform,dependencies,repository=sys.argv[1:]
by_filename={};by_identity={};copy=[]
def inspect(directory,required=False,existing=False):
    root=pathlib.Path(directory)
    if not root.is_dir():
        if required:raise ValueError('package directory missing: '+directory)
        return
    files=sorted(root.glob('*.deb'))
    if required and not files:raise ValueError('no debs in '+directory)
    for path in files:
        if not path.is_file() or path.is_symlink():raise ValueError('not a regular DEB: '+str(path))
        if any(c in path.name for c in '\n\r\t'):
            raise ValueError('unsupported DEB filename: '+repr(path.name))
        control=subprocess.check_output(['dpkg-deb','-f',str(path)],text=True)
        fields=dict(line.split(':',1) for line in control.splitlines() if ':' in line and not line.startswith((' ','\t')))
        identity=tuple(fields.get(key,'').strip() for key in ('Package','Version','Architecture'))
        package,version,arch=identity
        if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]+',package) or not version or not re.fullmatch(r'[a-z0-9][a-z0-9-]*',arch):
            raise ValueError('invalid package identity: '+str(path))
        if subprocess.run(['dpkg','--validate-version',version],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
            raise ValueError('invalid Debian version: '+version)
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
        digest=digest.hexdigest()
        if path.name in by_filename and by_filename[path.name]!=digest:
            raise ValueError('DEB filename collision with different content: '+path.name)
        if identity in by_identity and by_identity[identity]!=digest:
            raise ValueError('different content for identical package/version/architecture: '+repr(identity))
        first=path.name not in by_filename
        by_filename[path.name]=digest;by_identity[identity]=digest
        if first and not existing:copy.append(path.resolve())
try:
    inspect(repository,existing=True)
    inspect(platform,required=True)
    if dependencies:inspect(dependencies,required=True)
    for path in copy:sys.stdout.buffer.write(str(path).encode()+b'\0')
except (ValueError,OSError,subprocess.SubprocessError) as error:
    print('package publication refused: '+str(error),file=sys.stderr);sys.exit(1)
PYPACKAGES
    then
        rm -f "$COPY_PLAN"
        die "platform/dependency package validation failed; repository unchanged"
    fi
    ensure_key
    sudo mkdir -p "$REPO" || { rm -f "$COPY_PLAN"; die "cannot create package repository"; }
    local source
    while IFS= read -r -d '' source; do
        { sudo test -e "$REPO/$(basename "$source")" || copy_atomic "$source" "$REPO/$(basename "$source")"; } || { rm -f "$COPY_PLAN"; die "package copy failed: $source"; }
        echo "   + $(basename "$source")"
    done < "$COPY_PLAN"
    rm -f "$COPY_PLAN"

    # Build every index/signature privately. A failed indexing/signing pass
    # leaves the committed generation intact for clients already using it.
    # One scan, two indexes (see the header): main = isle-* and polari-*, deps/<release> = everything else.
    local METADATA COUNTS MAIN_COUNT DEPS_COUNT
    METADATA=$(mktemp -d) || die "cannot create private metadata stage"
    if ! ( sudo mkdir -p "$METADATA/main" "$METADATA/deps" && cd "$REPO" \
      && sudo nice -n 15 ionice -c 3 sh -c 'dpkg-scanpackages --multiversion . /dev/null > "$1/scanned" 2>/dev/null' sh "$METADATA" ); then
        sudo rm -rf -- "$METADATA"; die "index build failed"
    fi
    if ! COUNTS=$(sudo python3 "$SCRIPT_DIR/apt_index_split.py" "$METADATA/scanned" "$METADATA/main/Packages" "$METADATA/deps/Packages"); then
        sudo rm -rf -- "$METADATA"; die "index build failed"
    fi
    MAIN_COUNT=${COUNTS% *}; DEPS_COUNT=${COUNTS#* }
    if ! build_index "$METADATA/main" isle-mesh "Isle Mesh" isle "" "Isle-Mesh apt-on-mesh repo"; then
        sudo rm -rf -- "$METADATA"; die "$INDEX_ERROR"
    fi
    # Suite = the directory a source line names (deps/<release>): apt compares the two and warns when they differ.
    if [ "$DEPS_COUNT" != 0 ] && ! build_index "$METADATA/deps" isle-mesh-deps "Isle Mesh dependency set" "deps/$CODENAME" "$CODENAME" \
            "Operating system packages of the isle offline dependency set, built for $CODENAME"; then
        sudo rm -rf -- "$METADATA"; die "$INDEX_ERROR (dependency set)"
    fi
    sudo GNUPGHOME="$GNUPG" gpg --batch --yes --export -o "$(part_of "$REPO/$KEYRING_NAME")" \
        && commit_part "$REPO/$KEYRING_NAME" || die "archive keyring export failed"
    # serve the remote bootstrap + the root CA next to the debs (the
    # §5b one-flow: a fresh remote fetches these first)
    [ -f "$SCRIPT_DIR/isle-bootstrap.sh" ] && copy_atomic "$SCRIPT_DIR/isle-bootstrap.sh" "$REPO/isle-bootstrap.sh"
    [ -f "$SCRIPT_DIR/apt-install.sh" ] && copy_atomic "$SCRIPT_DIR/apt-install.sh" "$REPO/apt-install.sh"
    if [ -f "$CA" ]; then
        copy_atomic "$CA" "$REPO/isle-root.crt" || die "CA copy failed"
        sudo GNUPGHOME="$GNUPG" gpg --batch --yes --armor --detach-sign -o "$(part_of "$REPO/isle-bootstrap.sh.asc")" "$REPO/isle-bootstrap.sh" \
            && commit_part "$REPO/isle-bootstrap.sh.asc" || die "bootstrap signature failed"
        sudo GNUPGHOME="$GNUPG" gpg --batch --yes --armor --detach-sign -o "$(part_of "$REPO/isle-root.crt.asc")" "$REPO/isle-root.crt" \
            && commit_part "$REPO/isle-root.crt.asc" || die "public CA rotation signature failed"
    fi
    # ---- the HUMAN landing page (finding #9c, 2026-08-23):
    # https://apt.isle/ was a bare-nginx 403 — an average user sent
    # here (the store's join door, printed instructions) had no way
    # to discover the debs, the CA, or what to do. Regenerated on
    # every publish from what is actually served.
    local FPR
    FPR=$(openssl x509 -in "$CA" -noout -fingerprint -sha256 \
          2>/dev/null | cut -d= -f2 || echo "unavailable")
    DEB_ROWS=$(cd "$REPO" && for f in *.deb; do
        printf '<tr><td><a href="%s">%s</a></td><td>%s</td></tr>' \
            "$f" "$f" "$(du -h "$f" | cut -f1)"; done)
    write_atomic_from_stdin "$REPO/index.html" <<HTMLEOF
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isle apt repository</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;
      padding:0 1rem;line-height:1.5;color:#222}
 code,pre{background:#f4f4f4;padding:.15rem .35rem;border-radius:4px}
 pre{padding:.6rem;overflow-x:auto}
 table{border-collapse:collapse;width:100%}
 td{padding:.3rem .6rem;border-bottom:1px solid #eee}
 .fpr{word-break:break-all;font-size:.85em}
</style></head><body>
<h1>This isle's software</h1>
<p>Signed apt repository served by this isle's core.
   Everything here is installable two ways:</p>
<h2>Join this isle (new device)</h2>
<pre>curl -ko isle-bootstrap.sh https://apt.isle/isle-bootstrap.sh
# VERIFY its sha256 against the core's printout, then:
sudo bash isle-bootstrap.sh --fingerprint '&lt;from the core&gt;'</pre>
<p>The CA fingerprint is the trust anchor — always compare it.
   This core's CA (<a href="isle-root.crt">isle-root.crt</a>)
   fingerprint:</p>
<p class="fpr"><code>$FPR</code></p>
<h2>Already a member — standard versioned update</h2>
<pre>isle update plan
sudo isle update apply &lt;reviewed-release-id&gt;</pre>
<p>Older CLI without update support: authenticate the bootstrap using the archive key
already installed on this member. Never download a replacement key over an untrusted channel.</p>
<pre>curl --fail --http1.1 -o isle-bootstrap.sh https://apt.isle/isle-bootstrap.sh
curl --fail --http1.1 -o isle-bootstrap.sh.asc https://apt.isle/isle-bootstrap.sh.asc
gpgv --keyring /usr/share/keyrings/isle-archive-keyring.gpg isle-bootstrap.sh.asc isle-bootstrap.sh &amp;&amp;
sudo bash isle-bootstrap.sh --update --release &lt;reviewed-release-id&gt;</pre>
<p>If current TLS trust cannot authenticate the core after a reset, obtain the public
root fingerprint independently from the Isle operator and use supported fingerprint
recovery. Never accept an unauthenticated fetched root.</p>
<h2>Packages served</h2>
<table>$DEB_ROWS</table>
<p>Index files: <a href="Packages">Packages</a> ·
   <a href="Release">Release</a> ·
   <a href="isle-archive-keyring.gpg">archive keyring</a></p>
<p>The index lists the isle and polari packages. Operating system packages of the offline
   dependency set are indexed apart, per Ubuntu release, under <code>deps/</code>; only
   <code>isle apt install</code> reads them, on a device of that release.</p>
</body></html>
HTMLEOF
    sudo chmod -R a+rX,go-w "$REPO"
    # The dependency set goes first: a package leaves the main index only after deps/<release>/ lists it.
    if [ "$DEPS_COUNT" != 0 ] && ! commit_index "$METADATA/deps" "$REPO/deps/$CODENAME"; then
        sudo rm -rf -- "$METADATA"; die "atomic metadata publication failed (dependency set)"
    fi
    if ! commit_index "$METADATA/main" "$REPO"; then
        sudo rm -rf -- "$METADATA"; die "atomic metadata publication failed"
    fi
    sudo rm -rf -- "$METADATA"
    ok "repo indexed + signed ($(ls "$REPO"/*.deb | wc -l) debs: $MAIN_COUNT isle and polari packages in the main index, $DEPS_COUNT operating system packages in deps/$CODENAME) + landing page"

    # serve: an nginx on the agent net at apt.isle (bind-mounted, so
    # re-publish is live without redeploy)
    # Storage contract 4/6: once the placement is cutover|hosting another device serves apt.isle; the core must
    # not resurrect its private compose. The publish stays local and the hosting replica catches up by sync.
    local APT_PLACED
    APT_PLACED=$(python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1])).get("datasets", {})
    print(next((e.get("state", "") for e in d.values() if isinstance(e, dict) and e.get("domain") == "apt.isle"), ""))
except (OSError, ValueError, AttributeError):
    print("")' "${ISLE_PLACEMENT:-/etc/isle-mesh/storage/placement.json}" 2>/dev/null || true)
    if [ "$APT_PLACED" = "cutover" ] || [ "$APT_PLACED" = "hosting" ]; then
        ok "published on the core; hosting replica lags until sync"
    elif docker ps --format '{{.Names}}' | grep -q '^isle-apt-apt-1$\|^isle-apt\b'; then
        ok "apt.isle already serving (bind mount picks up the refresh)"
    else
        local COMPOSE="$APT_DIR/compose.yml"
        sudo tee "$COMPOSE" >/dev/null <<EOF
services:
  apt:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - $REPO:/usr/share/nginx/html:ro
EOF
        isle app deploy apt --compose "$COMPOSE" --service apt --port 80 \
            --domain apt.isle || die "apt.isle deploy failed"
        ok "apt.isle deployed"
    fi
    # finding #9b: the CORE host itself cannot reach its own agent (macvlan isolation). The pin is the
    # managed hosts-reconcile block's job (storage contract section 5): an unmanaged `127.0.0.1 apt.isle`
    # line would outlive a cutover and make the core resolve its own name forever.
    bash "$SCRIPT_DIR/hosts-reconcile.sh" >/dev/null 2>&1 || warn "hosts-reconcile did not run; the core hairpin pin is applied by its next timer run"
    python3 "$SCRIPT_DIR/release_notice.py" "$REPO" || warn "Package publication succeeded, but remote update notification could not be published. Retry apt-repo publish after the backend is ready."
    python3 "$SCRIPT_DIR/storage_placement.py" after-publication --repo "$REPO" || echo "[storage] manifest step failed; local publication complete"
    echo "clients: isle apt-repo enable && sudo apt update"
    echo "humans:  https://apt.isle/  (debs + CA + join instructions)"
}

fetch_repo() { # $1 path, $2 outfile
    $CURL -o "$2" "$URL/$1" 2>/dev/null && return 0
    $CURL --resolve apt.isle:443:127.0.0.1 -o "$2" "$URL/$1" 2>/dev/null
}

enable_client() {
    [ -f "$CA" ] || die "isle CA not trusted here — run: isle trust fetch / isle onboard"
    # apt cannot --resolve: on the CORE HOST (macvlan isolation — it
    # can't reach its own agent's IP) pin apt.isle to the local proxy
    if ! $CURL -o /dev/null "$URL/InRelease" 2>/dev/null; then
        if $CURL --resolve apt.isle:443:127.0.0.1 -o /dev/null "$URL/InRelease" 2>/dev/null \
           && ! grep -q "apt\.isle" /etc/hosts; then
            # the managed block (hosts-reconcile) owns core pins; never an unmanaged line
            bash "$SCRIPT_DIR/hosts-reconcile.sh" >/dev/null 2>&1 || true
            ok "host isolation detected — applied the managed core hairpin pin for apt.isle"
        fi
    fi
    local tmp; tmp=$(mktemp)
    fetch_repo "$KEYRING_NAME" "$tmp" || die "cannot fetch the repo key from $URL (is the core's apt repo published?)"
    sudo install -m 644 "$tmp" "/usr/share/keyrings/$KEYRING_NAME"; rm -f "$tmp"
    ok "archive key trusted (fetched over the CA-authenticated channel)"
    # The pin goes in BEFORE the source line: the source is never on without it.
    write_pin
    ok "apt pin written: only isle-* and polari-* packages are taken from $URL"
    echo "deb [signed-by=/usr/share/keyrings/$KEYRING_NAME] $URL ./" \
        | sudo tee "$SOURCE_LIST" >/dev/null
    ok "apt source added: $URL (flat, signed)"
    echo "next: sudo apt update && sudo apt install isle-mesh-cli polari-shell-core isle-app-store"
}

# The repository also carries the operating system packages of the offline dependency set (built for ONE
# Ubuntu release). While they sat in the main index, without a pin, apt treated them like Ubuntu's own
# archive and a plain `apt install` on a device of another release took them (2026-11-17: libc6 replaced,
# the desktop removed). Two guards now: the main index lists isle-* and polari-* only (publish), and the
# pin lets apt take only isle-* and polari-* from this source whatever a core still serves.
pin_state() { # prints present | absent | differs
    [ -f "$PIN_DST" ] || { echo absent; return; }
    if cmp -s "$PIN_SRC" "$PIN_DST"; then echo present; else echo differs; fi
}
write_pin() {
    [ -f "$PIN_SRC" ] || die "the pin file is missing from this CLI: $PIN_SRC"
    sudo install -m 644 "$PIN_SRC" "$PIN_DST" || die "could not write $PIN_DST"
}
pin() {
    local state; state=$(pin_state)
    case "${1:-check}" in
        check)
            if [ "$state" = present ]; then
                ok "apt pin in place: only isle-* and polari-* packages are taken from the isle source"
                return 0
            fi
            if [ -f "$SOURCE_LIST" ]; then
                warn "apt pin $state and the isle source is ON: a plain apt install can take operating system packages from $URL"
            else
                warn "apt pin $state (the isle source is off on this device, so apt does not read $URL today)"
            fi
            echo "Type: sudo isle apt-repo pin apply"
            return 1 ;;
        apply)
            write_pin
            ok "apt pin written: $PIN_DST" ;;
        *) echo "usage: isle apt-repo pin [check|apply]"; return 2 ;;
    esac
}

status() {
    if [ -f "$SOURCE_LIST" ]; then echo "isle source on this device: on"; else echo "isle source on this device: off"; fi
    echo "apt pin (isle-* and polari-* only): $(pin_state)"
    local tmp; tmp=$(mktemp)
    if fetch_repo Packages "$tmp"; then
        echo "apt-on-mesh @ $URL serves:"
        grep -E "^Package:|^Version:" "$tmp" | paste - - | sed 's/Package: /  /;s/\tVersion: / /'
    elif [ -d "$REPO" ]; then
        echo "local repo dir (not reachable via $URL):"
        ls "$REPO"/*.deb 2>/dev/null | sed 's|.*/|  |'
    else
        warn "no apt repo reachable and none published here"
    fi
    # The operating system dependency set is served apart, per Ubuntu release; only isle apt install reads it.
    local name set; name=$(os_codename || true)
    if [ -d "$REPO/deps" ]; then
        for set in "$REPO"/deps/*/; do
            [ -f "$set/Packages" ] && echo "operating system dependency set for $(basename "$set"): $(grep -c '^Package:' "$set/Packages") packages"
        done
    elif [ -n "$name" ] && fetch_repo "deps/$name/Packages" "$tmp"; then
        echo "operating system dependency set for this device's release ($name): $(grep -c '^Package:' "$tmp") packages"
    else
        echo "operating system dependency set for this device's release (${name:-unknown}): none served"
    fi
    rm -f "$tmp"
}

case "${1:-help}" in
    snapshot) shift; exec python3 "$SCRIPT_DIR/repository_snapshot.py" "$@" ;;
    publish) shift; publish "$@" ;;
    enable)  enable_client ;;
    pin)     shift; pin "$@" ;;
    status)  status ;;
    *) echo "usage: isle apt-repo [publish [--from <dir>] [--dependencies-from <dir>]|snapshot|enable|pin [check|apply]|status]" ;;
esac
