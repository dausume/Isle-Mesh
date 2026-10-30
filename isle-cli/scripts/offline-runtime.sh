#!/bin/bash
# Shared offline checks. Read-only until the caller deliberately starts setup.
isle_offline() {
    [ "$(cat /etc/polari/install-mode 2>/dev/null)" = offline ]
}

isle_offline_medium() {
    local medium
    medium=$(cat /etc/polari/offline-source 2>/dev/null) || return 1
    [ -d "$medium" ] && [ -f "$medium/ISLE_OFFLINE_BUNDLE" ] || {
        echo "offline: reconnect the complete installation medium" >&2; return 1;
    }
    printf '%s\n' "$medium"
}

isle_offline_preflight() {
    local medium
    medium=$(isle_offline_medium) || return 1
    bash "$medium/scripts/verify-offline.sh" "$medium" || return 1
    # The staging installer checks dependency versions and loads images. Never
    # let a standalone offline DEB silently fall back to online setup instead.
    python3 - "$medium" <<'PY'
import json, re, subprocess, sys
medium = sys.argv[1]
manifest = json.load(open(medium + '/manifest.json'))
for dependency in manifest['requiredPackages']:
    satisfied = False
    for alternative in dependency.split('|'):
        pkg, op, wanted = re.fullmatch(r'([^ (]+)(?: \(([^ ]+) ([^)]+)\))?', alternative.strip()).groups()
        result = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}\t${Version}', pkg], capture_output=True, text=True)
        state, _, version = result.stdout.partition('\t')
        if result.returncode == 0 and state == 'installed' and (not op or subprocess.run(['dpkg', '--compare-versions', version.strip(), op, wanted]).returncode == 0):
            satisfied = True
            break
    if not satisfied:
        sys.exit('offline apt/: dependency no longer satisfied: ' + dependency + '; run the medium installer')
for entry in json.load(open(medium + '/images/images.json')):
    ref = entry['name'] + ':' + entry['tag']
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != entry['id']:
        sys.exit('offline: stage the complete medium through its installer first; missing/incorrect image ' + ref)
PY
}

isle_offline_image() {
    local medium
    medium=$(isle_offline_medium) || return 1
    python3 - "$medium" "$1" <<'PY'
import json, subprocess, sys
medium, ref = sys.argv[1:]
entries = json.load(open(medium + '/images/images.json'))
matches = [e for e in entries if e['name'] + ':' + e['tag'] == ref]
if len(matches) != 1:
    sys.exit('offline images/: image not in this release: ' + ref)
result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref], capture_output=True, text=True)
if result.returncode or result.stdout.strip() != matches[0]['id']:
    sys.exit('offline images/: missing/incorrect image ' + ref + '; run the medium installer')
PY
}

# App distribution may use the trusted Isle supply while internet access is
# disabled. Only an explicitly digest-pinned reference at registry.isle is
# eligible; mutable tags and external registries still require the medium.
isle_registry_digest_origin() {
    python3 - "$1" <<'PY'
import re, sys
match = re.fullmatch(r'(registry\.isle(?::([1-9][0-9]{0,4}))?)/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}', sys.argv[1])
if not match or (match[2] and int(match[2]) > 65535):
    sys.exit(1)
print('https://' + match[1])
PY
}

isle_pinned_image_present() {
    local ref="$1" digests
    digests=$(docker image inspect --format '{{json .RepoDigests}}' "$ref" 2>/dev/null) || return 1
    python3 - "$ref" "$digests" <<'PY'
import json, sys
try:
    digests = json.loads(sys.argv[2])
    valid = isinstance(digests, list) and sys.argv[1] in digests
except ValueError:
    valid = False
sys.exit(0 if valid else 1)
PY
}

isle_prepare_offline_app_image() {
    local ref="$1" origin
    if origin=$(isle_registry_digest_origin "$ref"); then
        isle_pinned_image_present "$ref" && return 0
        # No redirects or fallback hosts: validate the Isle root and requested
        # registry name before allowing Docker to obtain the exact digest.
        curl -fsS --cacert /etc/isle-mesh/ca/isle-root.crt --max-time 10 \
            "$origin/v2/" >/dev/null || {
                echo "offline Isle supply: trusted registry access failed; no internet fallback" >&2
                return 1
            }
        docker pull "$ref" || return 1
        isle_pinned_image_present "$ref" || {
            echo "offline Isle supply: pulled image does not report the requested repository digest" >&2
            return 1
        }
    else
        isle_offline_image "$ref" || {
            echo "offline app image must be supplied by the verified medium or pinned as registry.isle[:port]/repository@sha256:<digest>" >&2
            return 1
        }
    fi
}
