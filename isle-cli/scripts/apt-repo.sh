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
#   isle apt-repo status                   what the repo serves
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
    # Serialise automatic dependency publications and interactive publishes.
    # Readers never need this lock: committed InRelease + by-hash is immutable.
    command -v flock >/dev/null 2>&1 || die "flock is required for repository publication"
    sudo mkdir -p "$APT_DIR" || die "cannot create apt state directory"
    sudo touch "$APT_DIR/.publish.lock" && sudo chmod 644 "$APT_DIR/.publish.lock" \
        || die "cannot prepare publication lock"
    local PUBLISH_LOCK_FD
    exec {PUBLISH_LOCK_FD}<"$APT_DIR/.publish.lock" || die "cannot open publication lock"
    flock "$PUBLISH_LOCK_FD" || die "cannot lock repository publication"

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
        sudo cp -n -- "$source" "$REPO/" || { rm -f "$COPY_PLAN"; die "package copy failed: $source"; }
        echo "   + $(basename "$source")"
    done < "$COPY_PLAN"
    rm -f "$COPY_PLAN"

    # Build every index/signature privately. A failed indexing/signing pass
    # leaves the committed generation intact for clients already using it.
    local METADATA
    METADATA=$(mktemp -d) || die "cannot create private metadata stage"
    if ! ( cd "$REPO" && sudo sh -c 'dpkg-scanpackages --multiversion . /dev/null > "$1/Packages" 2>/dev/null' sh "$METADATA" \
      && sudo gzip -kf "$METADATA/Packages" \
      && cd "$METADATA" \
      && sudo sh -c 'apt-ftparchive -o APT::FTPArchive::Release::Origin=isle-mesh \
          -o APT::FTPArchive::Release::Label="Isle Mesh" \
          -o APT::FTPArchive::Release::Suite=isle \
          -o APT::FTPArchive::Release::Acquire-By-Hash=yes \
          -o APT::FTPArchive::Release::Description="Isle-Mesh apt-on-mesh repo" \
          release . > Release' ); then
        sudo rm -rf -- "$METADATA"; die "index build failed"
    fi
    if ! grep -qx 'Acquire-By-Hash: yes' "$METADATA/Release"; then
        sudo rm -rf -- "$METADATA"; die "index builder did not enable Acquire-By-Hash"
    fi
    if ! sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes -abs -o "$METADATA/Release.gpg" "$METADATA/Release"; then
        sudo rm -rf -- "$METADATA"; die "Release signing failed"
    fi
    if ! sudo GNUPGHOME="$GNUPG" gpg --batch --pinentry-mode loopback --passphrase "" --yes --clearsign -o "$METADATA/InRelease" "$METADATA/Release"; then
        sudo rm -rf -- "$METADATA"; die "InRelease signing failed"
    fi
    sudo GNUPGHOME="$GNUPG" gpg --batch --yes --export -o "$REPO/$KEYRING_NAME"
    # serve the remote bootstrap + the root CA next to the debs (the
    # §5b one-flow: a fresh remote fetches these first)
    [ -f "$SCRIPT_DIR/isle-bootstrap.sh" ] && sudo cp "$SCRIPT_DIR/isle-bootstrap.sh" "$REPO/"
    [ -f "$SCRIPT_DIR/apt-install.sh" ] && sudo cp "$SCRIPT_DIR/apt-install.sh" "$REPO/"
    [ -f "$CA" ] && sudo cp "$CA" "$REPO/isle-root.crt"
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
    sudo tee "$REPO/index.html" >/dev/null <<HTMLEOF
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
<h2>Already a member — apt route</h2>
<pre>isle apt-repo enable
sudo apt update &amp;&amp; sudo apt install isle-app-store</pre>
<h2>Packages served</h2>
<table>$DEB_ROWS</table>
<p>Index files: <a href="Packages">Packages</a> ·
   <a href="Release">Release</a> ·
   <a href="isle-archive-keyring.gpg">archive keyring</a></p>
</body></html>
HTMLEOF
    sudo chmod -R a+rX,go-w "$REPO"
    # Publish immutable hash-addressed indexes BEFORE changing InRelease.
    # Previous hashes are retained, so a client holding the previous signed
    # release can still finish while a newer generation becomes current.
    if ! sudo python3 - "$METADATA" "$REPO" <<'PYPUBLISH'
import hashlib,os,pathlib,shutil,sys,tempfile
stage,repo=map(pathlib.Path,sys.argv[1:])
def digest(path,algorithm='SHA256'):
    result=hashlib.new(algorithm.lower())
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):result.update(block)
    return result.hexdigest()
def prepared(source,directory):
    fd,name=tempfile.mkstemp(prefix='.publish-',dir=directory)
    os.close(fd)
    try:shutil.copyfile(source,name);os.chmod(name,0o644)
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
    then
        sudo rm -rf -- "$METADATA"; die "atomic metadata publication failed"
    fi
    sudo rm -rf -- "$METADATA"
    ok "repo indexed + signed ($(ls "$REPO"/*.deb | wc -l) debs) + landing page"

    # serve: an nginx on the agent net at apt.isle (bind-mounted, so
    # re-publish is live without redeploy)
    if docker ps --format '{{.Names}}' | grep -q '^isle-apt-apt-1$\|^isle-apt\b'; then
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
    # finding #9b: the CORE host itself cannot reach its own agent
    # (macvlan isolation) and the catalog-driven hairpin reconcile
    # only helps once the app reaches the catalog — pin here too,
    # same precedent as enable_client. Idempotent.
    if ! getent hosts apt.isle >/dev/null 2>&1; then
        echo "127.0.0.1 apt.isle" | sudo tee -a /etc/hosts >/dev/null
        ok "core hairpin: pinned apt.isle -> 127.0.0.1 in /etc/hosts"
    fi
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
            echo "127.0.0.1 apt.isle" | sudo tee -a /etc/hosts >/dev/null
            ok "host isolation detected — pinned apt.isle → 127.0.0.1 in /etc/hosts"
        fi
    fi
    local tmp; tmp=$(mktemp)
    fetch_repo "$KEYRING_NAME" "$tmp" || die "cannot fetch the repo key from $URL (is the core's apt repo published?)"
    sudo install -m 644 "$tmp" "/usr/share/keyrings/$KEYRING_NAME"; rm -f "$tmp"
    ok "archive key trusted (fetched over the CA-authenticated channel)"
    echo "deb [signed-by=/usr/share/keyrings/$KEYRING_NAME] $URL ./" \
        | sudo tee /etc/apt/sources.list.d/00-isle-mesh.list >/dev/null
    ok "apt source added: $URL (flat, signed)"
    echo "next: sudo apt update && sudo apt install isle-mesh-cli polari-shell-core isle-app-store"
}

status() {
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
    rm -f "$tmp"
}

case "${1:-help}" in
    publish) shift; publish "$@" ;;
    enable)  enable_client ;;
    status)  status ;;
    *) echo "usage: isle apt-repo [publish [--from <dir>] [--dependencies-from <dir>]|enable|status]" ;;
esac
