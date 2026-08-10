# router-setup/images/

**This directory is intentionally empty in git.** Router VM images and
the upstream OpenWRT base are build artifacts, not source — they are
`.gitignore`d and never committed.

## How it gets populated

Run the acquisition script; it tries every source until one works:

```sh
../get-router-image.sh
```

1. **cache** — an image already here, checksum-checked
2. **mesh** — our prebuilt copy over the isle (no internet needed)
3. **release** — our public copy (internet, no isle needed)
4. **build** — built from the upstream OpenWRT base, which is the
   commonly-available artifact anyone can fetch from
   `downloads.openwrt.org`

`router-init` calls this automatically, so a fresh clone needs no
manual step. To build from upstream explicitly:

```sh
../build-router-image.sh
```

Everything the four sources have to agree on — versions, URLs,
checksums — lives in the tracked `../router-image.manifest`.

## Why images are not committed

Two reasons, and the second is the important one:

1. **Size.** The qcow2 is ~28 MB and changes on every rebuild. Git
   keeps every version forever, so committing it repeatedly cost
   ~170 MB of history for one 28 MB file.

2. **Secrets.** An image that has been *booted and provisioned* is not
   a build artifact — it is a running machine's disk. It contains
   `/etc/dropbear/` host private keys, `/etc/shadow` with the root
   password hash, `/etc/uhttpd.key`, and `authorized_keys`. Committing
   one to a public repo publishes all of that, and every router
   deployed from it shares the same host identity.

Images produced by `build-router-image.sh` are **pristine** — upstream
userland, no keys, no provisioning. Isle-specific setup (SSH key,
packages, uci config) happens at runtime against the booted VM, by
`router-init`. Only a pristine image may ever be published or cached
for others.
