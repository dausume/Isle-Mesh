# registry: the isle's image registry as a store app

A plain Docker registry, published and installed like any other store app. It answers at `registry.isle`
through the agent of the device it is installed on, with that app's own certificate from the isle's root. It
has no sign-in: every device of the isle may push to it and pull from it.

## Where the images live

The release declares one storage slot, `images`, of the role `registry`. A device keeps the slot on a partition
that carries that role, and the app is started only when the slot is bound. The first install keeps the
declaration and refuses; bind the slot and install again:

    sudo isle store install registry
    sudo isle storage slot bind registry images --partition <filesystem-identifier>
    sudo isle store install registry

Its data is then the folder `isle-slots/registry/images` on that partition. No partition is chosen by the CLI.

## Uploads

Image layers are far larger than the agent's default request body (1 MiB), so the release declares
`deploy.maxUpload: "0"` (no limit). The device's agent writes that for this app only.

## Build, publish, install

    isle store build registry <output folder>
    sudo isle store publish <output folder>/release.json

The build takes the image named by `buildFrom` (`registry:2`) from the machine it runs on and downloads nothing;
`--pull` is the explicit choice to fetch it. It prints the `manifestSha256` that publish must print too.

## The older verb

`isle registry setup` starts a registry by hand on the core, on a port of its own and outside the store. This
release is the same registry as an app. Both take the name `registry.isle`: use one of them, not both.
