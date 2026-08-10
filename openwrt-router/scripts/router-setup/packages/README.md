# router-setup/packages/

**This directory is intentionally empty in git.** The `.ipk` files are
build artifacts fetched from upstream, not source — they are
`.gitignore`d and never committed.

## How it gets populated

`router-init` calls `download_openwrt_packages` (step 7), which
resolves each required package and its dependencies from
`downloads.openwrt.org` for the pinned OpenWRT version and drops the
`.ipk` files here. Packages already present are left alone, so the
directory doubles as an offline cache: populate it once and later runs
need no internet.

The required set is declared in
`router-init-lib/70-download-packages.sh` (`REQUIRED_PACKAGES`).

To populate it by hand:

```sh
../download-packages.sh
```

## Why they are not committed

The standing rule: no build artifacts in git. These are ~1.5 MB of
binaries that are re-downloadable from upstream at any time, and git
would keep every version of them forever. See `../images/README.md`
for the same reasoning applied to the router VM image, where the cost
is much higher.
