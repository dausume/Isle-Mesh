# file-store: one file store on one partition, as a store app

The stock SeaweedFS image, started as one process that holds everything a store needs: its index and its content.
It is published and installed like any other store app. It answers at `<instance>.isle` (the first one at
`file-store.isle`) through the agent of the device it is installed on, with that app's own certificate from the
isle's root. What answers there is the S3 door of the store. It has no sign-in: every device of the isle may read
and write.

A file is kept once, on the partition of its store, and every device reads it by the isle name of that store.
An isle has as many stores as it has partitions that hold files this way; each is one install of this release
under a name of its own.

## Where the files live

The release declares one storage slot, `files`, of the role `blob`. A device keeps the slot on a partition that
carries that role, and the app is started only when the slot is bound. The first install keeps the declaration
and refuses; bind the slot and install again:

    sudo isle store install file-store
    sudo isle storage slot bind file-store files --partition <filesystem-identifier>
    sudo isle store install file-store

Its data is then the folder `isle-slots/file-store/files` on that partition: the index (`filerldb2`), the record
of the store itself (`m9333`) and the content (`*.dat`, `*.idx`). A disk carried to another machine brings the
whole store, names included. The slot names its owner, 1000:1000, which is the user the image runs as. So the
folder is made for that user, and the container changes no ownership when it starts (it would, on a folder that
belongs to root).

## What is open

| Listener | Port | Who reaches it |
|---|---|---|
| The S3 door | 8333 | The device's agent, and so the isle by the store's name |
| The gRPC port of the S3 door | 18333 | The containers on the agent's network of the same device; the agent passes nothing to it |
| Master, volume server, filer and their gRPC ports | 9333, 8080, 8888, 19333, 18080, 18888 | Only the store itself, inside its container |

The two extra listeners of the S3 door (Iceberg and Lance) are switched off. No port of the machine is opened.

## Uploads

Files are larger than the agent's default request body (1 MiB), so the release declares
`deploy.maxUpload: "0"` (no limit). The device's agent writes that for this app only.

## Stopping

The program needs about 20 seconds to stop cleanly, so the compose file gives it 60 (`stop_grace_period`).

## Build, publish, install

    isle store build file-store <output folder>
    sudo isle store publish <output folder>/release.json

The build takes the image named by `buildFrom` (`chrislusf/seaweedfs:4.47`) from the machine it runs on and
downloads nothing; `--pull` is the explicit choice to fetch it. It prints the `manifestSha256` that publish must
print too. The built folder carries the image (508 MiB as a file), so a machine that never had it can install
the store.

## Not in this release

- A link from one store to a file space of another (the copy that points back). The program refuses a link to an
  address inside the isle unless its named options are given (`-filer.allowUntrustedRemoteEndpoints` and the two
  like it); this release does not give them.
- A folder of the machine that shows a file space (the mount for KiCad, FreeCAD and Blender). It needs the filer
  reachable from the machine itself, which this release keeps inside the container.
