# esp-engines: the ESP32-C3 engines worker as a store app

The ESP32-C3 engines worker of the Electronics app. It builds ESP-IDF projects and runs them on Espressif's
QEMU fork with the Polari pin model. It takes batch requests only and never flashes a board.

## Build, publish, install

The image is built from the suite's tracked `polari-rf-node/prf-esp-engines/Dockerfile` as `prf-esp-engines:noble`.
Then `isle store build` pins it; it pulls nothing, so the image must be on the machine that builds:

    isle store build <this folder> <output folder>
    sudo isle store publish <output folder>/release.json

A device gets it with `isle release build ... --store-app <device>:<output folder>`. After install the engine
binds on the backend's boot, or at once with `pol engines sync`.

## The twin door

From 2026.11.23-1 the worker also holds twin sessions of a C3 and advertises its door
(`TWIN_DOOR_URL=wss://twin.esp-engines.isle`). The door is the app's second service, `twin-door`: the same image
with `python3 /srv/twin_door.py` on 9864, reaching the worker by its service name. `isle store install` starts it
with the worker, so a release packet needs no extra step for it.

Its name is a door the release declares (`"doors": [{"name": "twin", "service": "twin-door", "port": 9864}]`):
`isle store install` passes it to `isle app deploy --door twin=twin-door:9864`, which registers
`twin.<the app's domain>` (here `twin.esp-engines.isle`) with the agent under the entry `esp-engines--twin`, with its
own certificate, publishes its DNS like the app's own name and checks it answers over trusted HTTPS. The core counts
it as a door of the app, never as another instance. `isle app undeploy` removes it.

## Uploads

A run posts the 4 MB flash image as base64, and the agent's default request limit is 1 MiB. The release
declares `deploy.maxUpload: "32m"`, written for this app only.

## Limits

2 GiB of memory, 3 CPUs and 512 processes, sized for a member with 4 cores and about 4 GiB free.

## Long requests

The agent drops a proxied request that sends no byte for 60 s. The worker keeps a long request alive by
writing one space every `WORKER_KEEPALIVE_S` seconds (20). `WORKER_MAX_MB` (16) is the ceiling for what one run gives back
(files, stdout and stderr together); a run past it is refused, never cut.
