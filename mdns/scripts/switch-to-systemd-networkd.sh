#!/bin/bash
# Compatibility entrypoint for existing mDNS setup callers.
# The old implementation started a second DHCP client on the normal
# default-route interface and changed global resolver configuration.
# Normal internet interfaces stay with their existing OS network owner in
# every install mode. Dedicated Isle radio, cable and bridge setup is separate.
# This prevents new takeover configuration; it does not migrate old artifacts.
set -eu
printf '%s\n' 'Keeping the normal internet uplink with its existing OS network owner.'
printf '%s\n' 'Dedicated Isle interfaces are configured separately.'
