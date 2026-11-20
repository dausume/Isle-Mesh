# isle-content-host

Hosting app (catalog kind `mesh-app`, `category: "infrastructure"`) that serves one proven storage dataset
read-only over HTTP. Contract: `docs/STORAGE-MIGRATION-CONTRACT.md` section 7.

## What it serves

- `/` serves `/data/current` (nginx `root`, `autoindex on`): the published generation of the dataset.
- `/.well-known/isle-storage/` serves `/data/control/` (`latest.json`, `manifest-<manifestSha>.json`), JSON,
  `Cache-Control: no-store`, no directory listing.
- `/.well-known/isle-storage/current` is the one-line `control/current` file holding the contentSha (`text/plain`).
  It is also the health path.
- Nothing else under the dataset root (`store/`, `staging/`, `binding.json`) is reachable.
- Port 80 only: the isle agent terminates TLS. `server_tokens off`; request bodies are never logged.

## Bind contract

`isle app deploy --bind ISLE_DATASET_PATH=<abs dir>` makes the overlay (`app_overlay.py`) add the long-syntax bind
`<dir>:/data` read-only with `create_host_path: false` and set `ISLE_DATASET_PATH` in the service environment.
The compose file interpolates `${ISLE_DATASET_PATH}` itself so the same bind is declared in the release.
The `storage` block of `release.json` (`bindParameter`, `mount`, `readOnly`) is metadata for the storage tooling;
it is an extra key that the verifier and publisher do not read (they copy it through unchanged).

- `create_host_path: false`: if the dataset directory is missing the deploy must fail, never silently create an
  empty directory on the root filesystem and serve nothing.
- `restart: "no"`: the app must not come back on its own after a reboot or a dataset unmount; the agent starts it
  only once the dataset is proven and mounted.

## Pins and the hostingRelease

The source tree carries placeholders (marked by `PINS-PENDING`): ref `isle-content-host/nginx:PIN-AT-BUILD` and all-zero
`configDigest` / `manifestDigest`. `build-release.sh <outdir>` pulls `nginx:alpine`, tags it `isle-content-host/nginx:<version>`
(the version in `release.json`), saves `images/nginx-alpine.tar` by that tag with `docker image save --platform linux/amd64` (Docker 25+/29; the build refuses without it), refuses an archive whose annotated root entry is an index, and derives `configDigest` and `manifestDigest`
from the archive with `image_identity.py` (the code behind `isle update describe-image`; `docker image inspect .Id` is never used,
it is the manifest digest on a containerd-store host). It refuses unless the archive holds exactly one manifest, writes pinned
copies of `release.json` and `docker-compose.yml` (compose `image:` equals the ref, so `docker load` restores precisely what
compose runs and nothing is pulled), refuses if any placeholder remains, and prints `hostingRelease.manifestSha256` plus a JSON
block `{ref, configDigest, manifestDigest, indexDigest, archiveSha256, archiveBytes, manifestSha256}` for the release metadata.
`ISLE_SCRIPTS` overrides the scripts directory; if `image_identity.py` is absent the installed `isle update describe-image` is used.
`manifestSha256` is the sha256 of the manifest as `isle store publish` stores it (sources rewritten to url/sha256/size,
sorted-key JSON), so it must equal the sha256 printed by publish.

The release is the linux/amd64 leaf of `nginx:alpine`, not the multi-platform index. On a containerd-store host
`docker image inspect` shows the INDEX digest (reported informationally as `indexDigest`), which is NOT the `manifestDigest`
the plan pins (the amd64 image manifest).

`nginx:alpine` is a moving tag. The release publishes THESE bytes (the archive sha256 is recorded in the published manifest
through the normal archive publish path); never rebuild "to the same pin" later, publish a new version instead.
Publish on the core: `sudo isle store publish <outdir>/release.json` (not run by the build).

## Phase A

The core keeps serving and publishing the apt repository through `apt-repo.sh`; nothing here retires it.
