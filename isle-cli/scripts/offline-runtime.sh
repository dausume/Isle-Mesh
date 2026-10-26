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
