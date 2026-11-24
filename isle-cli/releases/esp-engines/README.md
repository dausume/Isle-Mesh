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

## The twin door (twin-door/)

From 2026.11.23-1 the worker also holds twin sessions of a C3 and advertises its door
(`TWIN_DOOR_URL=wss://twin.esp-engines.isle`). The door is a small app of its own, `twin-esp-engines`: the same
image with `python3 /srv/twin_door.py`, as the board worker's door `twin-board-engines` is. After the worker is
installed on its device:

    isle app deploy twin-esp-engines --compose twin-door/docker-compose.yml --service twin-door --port 9864 --domain twin.esp-engines.isle

Until the door is deployed, the backend refuses a C3 session in words: the worker names a door that does not answer.
The image tag in `twin-door/docker-compose.yml` follows `release.json`'s version.

## Uploads

A run posts the 4 MB flash image as base64, and the agent's default request limit is 1 MiB. The release
declares `deploy.maxUpload: "32m"`, written for this app only.

## Limits

2 GiB of memory, 3 CPUs and 512 processes, sized for a member with 4 cores and about 4 GiB free.

## Long requests

The agent drops a proxied request that sends no byte for 60 s. The worker keeps a long request alive by
writing one space every `WORKER_KEEPALIVE_S` seconds (20). `WORKER_MAX_MB` (16) is the ceiling for what one run gives back
(files, stdout and stderr together); a run past it is refused, never cut.
