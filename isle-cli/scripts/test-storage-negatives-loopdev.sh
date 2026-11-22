#!/usr/bin/env bash
# Root-only negative controls for the storage migration code (docs/STORAGE-MIGRATION-CONTRACT.md section 8), run against the
# REAL storage_receive / storage_preflight code on a real ext4 filesystem on a loop device.  Companion of
# test-storage-negatives.py, whose *__loopdev tests are the skeletons of what this script does.
#
#   sudo bash scripts/test-storage-negatives-loopdev.sh
#
# stdout is exactly one line per control:   PASS|FAIL|SKIP <control-name>: <one sentence>
# followed by:                              SUMMARY: <pass>/<total> passed, <fail> failed
# Everything else goes to stderr.  Exit 0 = no control failed, 1 = at least one FAILED, 2 = not root / setup failed before
# any control ran.
#
# Safety: everything happens under one mktemp -d directory below /tmp.  The only mount is that directory's own mnt/, the only
# loop device is the one attached to the sparse image created here.  /etc/isle-mesh, /etc/systemd and every real mount are
# never read as a target or written; the trap lazily unmounts and detaches only what this script created.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID_GOOD="11112222-3333-4444-8555-666677778888"
UUID_WRONG="00000000-dead-beef-0000-000000000000"
REL="isle/datasets/apt-public-repo"

if [ "$(id -u)" -ne 0 ]; then
    echo "test-storage-negatives-loopdev.sh needs root (loop device, mkfs, mount); run it with sudo." >&2
    exit 2
fi
for tool in losetup mkfs.ext4 mount umount findmnt python3 truncate mktemp; do
    command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 2; }
done

PASS=0; FAIL=0; SKIP=0; TOTAL=0
T=""; MNT=""; IMG=""; LOOP=""

emit() {  # status name sentence
    TOTAL=$((TOTAL + 1))
    case "$1" in PASS) PASS=$((PASS + 1));; FAIL) FAIL=$((FAIL + 1));; SKIP) SKIP=$((SKIP + 1));; esac
    printf '%s %s: %s\n' "$1" "$2" "$3"
}

cleanup() {
    set +e
    if [ -n "$MNT" ] && [ -n "$T" ] && mountpoint -q "$MNT" 2>/dev/null; then umount -l "$MNT" >&2 2>&1; fi
    if [ -n "$LOOP" ]; then losetup -d "$LOOP" >&2 2>&1; fi
    # a loop device detached lazily during a test may still be attached under another name: find it by backing file
    if [ -n "$IMG" ]; then
        for dev in $(losetup -j "$IMG" 2>/dev/null | cut -d: -f1); do losetup -d "$dev" >&2 2>&1; done
    fi
    if [ -n "$T" ] && [ -d "$T" ]; then rm -rf --one-file-system -- "$T"; fi
}
trap cleanup EXIT

T="$(mktemp -d /tmp/isle-negatives.XXXXXX)" || { echo "cannot create a temp dir" >&2; exit 2; }
MNT="$T/mnt"; IMG="$T/disk.img"
case "$MNT" in /tmp/isle-negatives.*/mnt) ;; *) echo "refusing odd mountpoint $MNT" >&2; exit 2;; esac
mkdir "$MNT" "$T/outside"
truncate -s 512M "$IMG"
mkfs.ext4 -q -F -U "$UUID_GOOD" "$IMG" >&2 || { echo "mkfs.ext4 failed" >&2; exit 2; }

attach_and_mount() {
    LOOP="$(losetup -f --show "$IMG")" || return 1
    mount -t ext4 "$LOOP" "$MNT" >&2 || return 1
}
detach_and_unmount() {
    if mountpoint -q "$MNT"; then umount "$MNT" >&2; fi
    if [ -n "$LOOP" ]; then losetup -d "$LOOP" >&2 || true; LOOP=""; fi
}
attach_and_mount || { echo "cannot attach and mount the loop image" >&2; exit 2; }

# py_result : python reads the check on stdin (PY_COMMON is prepended) and prints "OK" or "FAIL: reason" as its last line;
# the last line lands in $RESULT.  Nothing the python code prints reaches stdout of this script except through emit.
py_result() {
    local out
    out="$({ printf '%s\n' "$PY_COMMON"; cat; } | LOOPDEV="$LOOP" MNT="$MNT" T="$T" REL="$REL" UUID_GOOD="$UUID_GOOD" \
           UUID_WRONG="$UUID_WRONG" SCRIPTS="$SCRIPTS" python3 -I - 2>&2)" || true
    RESULT="$(printf '%s\n' "$out" | tail -n 1)"
    [ -n "$RESULT" ] || RESULT="FAIL: the check produced no result"
}

verdict() {  # name sentence [extra-problem]
    if [ "$RESULT" = "OK" ] && [ -z "${3:-}" ]; then emit PASS "$1" "$2"; else emit FAIL "$1" "${3:-${RESULT#FAIL: }}"; fi
}

PY_COMMON='
import hashlib, json, os, stat, subprocess, sys
sys.path.insert(0, os.environ["SCRIPTS"])
import storage_receive as sr
import storage_preflight as pf
MNT, REL, T = os.environ["MNT"], os.environ["REL"], os.environ["T"]
UUID_GOOD, UUID_WRONG = os.environ["UUID_GOOD"], os.environ["UUID_WRONG"]
def snap(root):
    out = {}
    for base, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(base, n); i = os.lstat(p)
            out[os.path.relpath(p, root)] = (stat.S_IFMT(i.st_mode), i.st_size,
                hashlib.sha256(open(p, "rb").read()).hexdigest() if stat.S_ISREG(i.st_mode) else (os.readlink(p) if stat.S_ISLNK(i.st_mode) else ""))
    return out
def finish(problem=None):
    print("OK" if problem is None else "FAIL: " + problem)
    sys.exit(0)
'

# ---------------------------------------------------------------- 1. wrong disk (UUID mismatch) on a real mounted filesystem
py_result <<'PY'
before = snap(MNT)
try:
    sr.MountRoot(MNT, UUID_WRONG)
    finish("MountRoot accepted a mounted filesystem whose UUID differs from the plan")
except sr.ReceiveError as e:
    if not e.retryable or "UUID" not in str(e):
        finish("MountRoot refused without a retryable UUID message: %r" % (e,))
try:
    pf.inspect(MNT + "/" + REL, UUID_WRONG, 1)
    finish("storage_preflight.inspect accepted the wrong UUID")
except ValueError as e:
    if "UUID" not in str(e):
        finish("preflight refused without the UUID words: %r" % (e,))
if snap(MNT) != before:
    finish("the filesystem changed during a refused attempt")
sr.MountRoot(MNT, UUID_GOOD).close()      # control: the right UUID is accepted
finish()
PY
verdict wrong_disk_uuid_mismatch "a real mounted ext4 with another UUID is refused by MountRoot and preflight and left byte-for-byte unchanged."

# ---------------------------------------------------------------- 2. unplug mid-copy (UUID/st_dev/path identity changes)
py_result <<'PY'
files = {"f%02d" % i: os.urandom(262144) for i in range(24)}
by_sha = {hashlib.sha256(v).hexdigest(): v for v in files.values()}
entries = [dict(path=p, kind="file", size=len(v), sha256=hashlib.sha256(v).hexdigest()) for p, v in sorted(files.items())]
doc = dict(schemaVersion=1, kind="isle-content-snapshot", totalBytes=sum(len(v) for v in files.values()), entries=entries)
parts = REL.split("/")
root = sr.MountRoot(MNT, UUID_GOOD)
calls = []
def fetch(entry, sink):
    sink.write(by_sha[entry["sha256"]])
    calls.append(entry["path"])
    if len(calls) == 3:                              # the unplug
        subprocess.run(["umount", "-l", MNT], check=True)
        subprocess.run(["losetup", "-d", os.environ["LOOPDEV"]], check=False)
try:
    sr.receive(root, parts, doc, fetch, op_id="unplug")
    finish("the receive completed although the disk was unplugged")
except sr.ReceiveError as e:
    if not e.retryable:
        finish("the unplug was not a retryable failure: %r" % (e,))
    if not any(w in str(e) for w in ("st_dev", "UUID", "mount", "Mount")):
        finish("the refusal does not name the mount: %r" % (e,))
if len(calls) != 3:
    finish("the copy kept fetching after the unplug (%d fetches)" % len(calls))
try:
    root.close()
except OSError:
    pass
finish()
PY
UNPLUG_RESULT="$RESULT"; UNPLUG_PROBLEM=""
LOOP=""
if attach_and_mount; then          # re-attach the same image and look at what the interrupted copy left on the disk
    py_result <<'PY'
base = os.path.join(MNT, REL)
if os.path.lexists(os.path.join(base, "current")) or os.path.lexists(os.path.join(base, "binding.json")):
    finish("current or binding.json exists after an interrupted copy")
store = os.path.join(base, "store")
for name in (os.listdir(store) if os.path.isdir(store) else []):
    if hashlib.sha256(open(os.path.join(store, name), "rb").read()).hexdigest() != name:
        finish("a partial or corrupt object %s sits in store/" % name)
finish()
PY
    [ "$RESULT" = "OK" ] || UNPLUG_PROBLEM="${RESULT#FAIL: }"
else
    UNPLUG_PROBLEM="the image could not be re-attached to inspect the disk after the unplug"
fi
RESULT="$UNPLUG_RESULT"
verdict unplugged_mid_copy "after the disk was lazily unmounted and its loop device detached mid-copy the receive stopped retryably and left no current, no binding and only verified objects." "$UNPLUG_PROBLEM"

# ---------------------------------------------------------------- 3. root filesystem fallback by unmounting
detach_and_unmount
mkdir -p "$MNT"
LISTING_BEFORE="$(find "$MNT" -mindepth 1 | sort | sha256sum)"
py_result <<'PY'
for label, call in (("preflight", lambda: pf.inspect(MNT + "/" + REL, UUID_GOOD, 1)), ("MountRoot", lambda: sr.MountRoot(MNT, UUID_GOOD))):
    try:
        r = call()
        if label == "MountRoot":
            r.close()
        finish("%s accepted a mountpoint that is not the planned filesystem" % label)
    except (ValueError, sr.ReceiveError) as e:
        if not any(w in str(e) for w in ("UUID", "root filesystem", "mount point", "mounted")):
            finish("%s refused without naming the mount/UUID: %r" % (label, e))
if os.listdir(MNT):
    finish("something was created below the unmounted mountpoint: %r" % os.listdir(MNT))
finish()
PY
LISTING_AFTER="$(find "$MNT" -mindepth 1 | sort | sha256sum)"
PROBLEM=""; [ "$LISTING_BEFORE" = "$LISTING_AFTER" ] || PROBLEM="the unmounted mountpoint changed"
verdict root_filesystem_fallback "with the disk unmounted, preflight and MountRoot refuse the bare mountpoint directory and nothing is created below it." "$PROBLEM"

# ---------------------------------------------------------------- 4. symlink in the path (on a mounted filesystem)
if attach_and_mount; then
    # Controls share one image: control 2 leaves a real <mount>/isle directory behind, and `ln -s` onto an
    # existing directory would create isle/outside INSIDE it (no symlink in the tested path). Start clean.
    rm -rf --one-file-system -- "${MNT:?}/isle"
    ln -s "$T/outside" "$MNT/isle"                  # <mount>/isle -> a directory outside the disk
    if [ ! -L "$MNT/isle" ]; then
        RESULT="FAIL: setup: $MNT/isle is not a symlink"
    else
    py_result <<'PY'
parts = REL.split("/")
try:
    pf.inspect(MNT + "/" + REL, UUID_GOOD, 1)
    finish("preflight accepted a path with a symlink component")
except ValueError as e:
    if "symlink" not in str(e):
        finish("preflight refused without the symlink words: %r" % (e,))
root = sr.MountRoot(MNT, UUID_GOOD)
try:
    root.dir_fd(parts[:2])
    finish("dir_fd followed a symlink component")
except sr.ReceiveError:
    pass
doc = dict(schemaVersion=1, kind="isle-content-snapshot", totalBytes=3,
           entries=[dict(path="a", kind="file", size=3, sha256=hashlib.sha256(b"abc").hexdigest())])
try:
    sr.receive(root, parts, doc, lambda e, s: s.write(b"abc"), op_id="symlink")
    finish("receive wrote through a symlink component")
except sr.ReceiveError:
    pass
root.close()
if os.listdir(os.path.join(T, "outside")):
    finish("bytes were written through the symlink: %r" % os.listdir(os.path.join(T, "outside")))
finish()
PY
    fi
    verdict symlink_in_the_path "a symlink component below the mount is refused by preflight, dir_fd and receive and nothing is written through it."
else
    emit FAIL symlink_in_the_path "the image could not be re-attached"
fi

# ---------------------------------------------------------------- 5, 6. controls this script cannot run without touching the system
emit SKIP unplugged_while_serving_bindsto "needs an isle-storage-<dataset>.service installed in /etc/systemd/system and a started stub host; this script never writes outside its temp dir."
emit SKIP hand_run_compose_up_with_disk_absent "needs docker and a deployed release bind; docker is not used by this script."

printf 'SUMMARY: %d/%d passed, %d failed\n' "$PASS" "$TOTAL" "$FAIL"
[ "$FAIL" -eq 0 ]
