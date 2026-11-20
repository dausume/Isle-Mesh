#!/usr/bin/env bash
# Build a publishable isle-content-host release into <outdir>.
#   usage: build-release.sh <outdir>
# Needs docker + network (pulls nginx:alpine). Run on a build host, not in tests.
# Then, on the core:  sudo isle store publish <outdir>/release.json
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:?usage: build-release.sh <outdir>}"
TAG="${NGINX_TAG:-nginx:alpine}"
ZERO="sha256:$(printf '0%.0s' $(seq 64))"
die() { echo "build-release: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
[ ! -e "$OUT/release.json" ] || die "$OUT/release.json already exists; use a fresh output dir"
mkdir -p "$OUT/images"

version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$SRC/release.json")"
[[ "$version" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad version in release.json (got '$version')"
ref="isle-content-host/nginx:${version}"   # immutable local tag; `docker load` restores exactly this tag

docker pull "$TAG" >&2
docker tag "$TAG" "$ref" >&2
# nginx:alpine is multi-platform; a plain save writes an OCI INDEX as the root entry, which has no single config.
# Save only the linux/amd64 leaf (its image manifest becomes the annotated root entry).
if ! save_err="$(docker image save --platform linux/amd64 -o "$OUT/images/nginx-alpine.tar" "$ref" 2>&1)"; then
    echo "$save_err" >&2
    if [[ "$save_err" == *"unknown flag"* || "$save_err" == *"--platform"* ]]; then
        die "docker image save --platform is required (Docker 25+/29); refusing a multi-platform archive"
    fi
    die "docker image save failed"
fi

# Refuse an archive whose annotated root entry is an index (multi-platform) before asking for its identity.
ARCHIVE="$OUT/images/nginx-alpine.tar"
root_check="$(tar -xOf "$ARCHIVE" index.json | python3 -c '
import json, sys
ref = sys.argv[1]
try: idx = json.load(sys.stdin)
except ValueError: sys.exit("archive index.json is unreadable")
hit = [m for m in idx.get("manifests", [])
       if m.get("annotations", {}).get("io.containerd.image.name") in (ref, "docker.io/" + ref)]
if not hit: sys.exit("no root index.json entry is annotated io.containerd.image.name=%s" % ref)
if hit[0].get("mediaType") in ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"):
    sys.exit("multi-platform archive: save with --platform linux/amd64")
' "$ref")" || die "${root_check:-archive index.json could not be inspected}"

# Informational: the index digest docker reports for the source tag (differs from manifestDigest on a containerd host).
index_raw="$(docker image inspect --format '{{index .RepoDigests 0}}' "$TAG" 2>/dev/null || true)"
index_digest="${index_raw##*@}"
[[ "$index_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || index_digest=""

# Identity comes from the ARCHIVE (never `docker image inspect .Id`, which is the manifest digest on a
# containerd-store host): the same describe code the update plan uses.
SCRIPTS="${ISLE_SCRIPTS:-$SRC/../../scripts}"
if [ -f "$SCRIPTS/image_identity.py" ]; then
    desc="$(python3 "$SCRIPTS/image_identity.py" "$OUT/images/nginx-alpine.tar" "$ref")" || die "describe failed"
elif command -v isle >/dev/null; then
    desc="$(isle update describe-image "$OUT/images/nginx-alpine.tar" "$ref")" || die "describe-image failed"
else
    die "neither $SCRIPTS/image_identity.py nor the isle CLI is available"
fi
ids="$(python3 -c '
import json, re, sys
d = json.loads(sys.argv[1]); ref = sys.argv[2]
pat = re.compile(r"^sha256:[0-9a-f]{64}$")
if d.get("ref") != ref: sys.exit("describe returned ref %r, expected %r" % (d.get("ref"), ref))
for k in ("configDigest", "manifestDigest"):
    if not pat.match(str(d.get(k, ""))): sys.exit("describe reported no usable %s (need exactly one manifest)" % k)
print(d["configDigest"], d["manifestDigest"])
' "$desc" "$ref")" || die "$ids"
config_digest="${ids% *}"; manifest_digest="${ids#* }"

# Rewrite the placeholders in copies; the source tree is never modified.
sed -e "s#isle-content-host/nginx:PIN-AT-BUILD#${ref}#" -e '/^# PIN-AT-BUILD/d' "$SRC/docker-compose.yml" > "$OUT/docker-compose.yml"
cp "$SRC/nginx.conf" "$OUT/nginx.conf"
python3 - "$SRC/release.json" "$OUT/release.json" "$ref" "$config_digest" "$manifest_digest" "$ZERO" <<'PY'
import json, sys
src, dst, ref, cfg, man, zero = sys.argv[1:]
m = json.load(open(src))
for image in m["images"]:
    if image["ref"] == "isle-content-host/nginx:PIN-AT-BUILD": image["ref"] = ref
    if image["configDigest"] == zero: image["configDigest"] = cfg
    if image.get("manifestDigest") == zero: image["manifestDigest"] = man
json.dump(m, open(dst, "w"), indent=2); open(dst, "a").write("\n")
PY

# Refuse to continue while any placeholder remains anywhere in the output.
if grep -rqE 'sha256:0{64}|PIN-AT-BUILD' "$OUT/release.json" "$OUT/docker-compose.yml"; then
    die "placeholder digest still present; refusing to publish"
fi
grep -q "image: ${ref}\$" "$OUT/docker-compose.yml" || die "compose image is not the release ref; refusing to publish"
rm -f "$OUT/PINS-PENDING"

compose_sha="$(sha256sum "$OUT/docker-compose.yml" | cut -d' ' -f1)"
conf_sha="$(sha256sum "$OUT/nginx.conf" | cut -d' ' -f1)"
archive_sha="$(sha256sum "$OUT/images/nginx-alpine.tar" | cut -d' ' -f1)"
archive_bytes="$(stat -c %s "$OUT/images/nginx-alpine.tar")"

# `isle store publish` rewrites every {source} to {url,sha256,size} and stores the
# manifest as sort_keys/indent=2 JSON; its sha256 is the release identity. That
# transform is deterministic, so compute it here (it must equal the sha256 printed by publish).
manifest_sha="$(python3 - "$OUT" <<'PY'
import hashlib, json, sys
from pathlib import Path
out = Path(sys.argv[1]); m = json.loads((out / "release.json").read_text())
base = "https://apt.isle/catalog/%s/%s/" % (m["name"], m["version"])
def pub(d):
    p = out / d.pop("source"); data = p.read_bytes(); h = hashlib.sha256(data).hexdigest()
    d.update(url=base + h + p.suffix, sha256=h, size=len(data))
for i in m["images"]: pub(i["archive"])
pub(m["compose"])
for f in m.get("files", []): pub(f)
raw = (json.dumps(m, sort_keys=True, indent=2) + "\n").encode()
print(hashlib.sha256(raw).hexdigest())
PY
)"
echo "hostingRelease.name           = isle-content-host"
echo "hostingRelease.version        = ${version}"
echo "hostingRelease.manifestSha256 = ${manifest_sha}"
python3 - "$ref" "$config_digest" "$manifest_digest" "$index_digest" "$archive_sha" "$archive_bytes" "$manifest_sha" <<'PY'
import json, sys
k = ("ref", "configDigest", "manifestDigest", "indexDigest", "archiveSha256", "archiveBytes", "manifestSha256")
v = list(sys.argv[1:]); v[5] = int(v[5]); v[3] = v[3] or None
print(json.dumps(dict(zip(k, v)), indent=2))
PY
echo "compose=$compose_sha nginx.conf=$conf_sha" >&2
echo "next: sudo isle store publish $OUT/release.json   (its printed sha256 must match the manifestSha256 above)" >&2
