# Preparing local app-hosting capabilities

`isle capabilities profiles` lists baseline profiles. `isle capabilities plan <profile>` is read-only: it reports missing packages, the retained service provider and the local setup command. `sudo isle capabilities enable <profile>` installs only missing prerequisites, enables the service, publishes a dated report and checks readiness. It never accepts a remote target or deploys an application.

| Profile | Baseline | Additional app checks |
| --- | --- | --- |
| mesh-app | Docker Engine, Docker Compose, joined agent | images, memory/storage, application services |
| polari-instance | Docker Engine, Docker Compose, joined agent | Polari images, modules, node placement |
| hardware-vm | architecture-appropriate QEMU, libvirt, usable KVM | firmware virtualization, guest image, resources, passthrough |
| shell-app | shared Polari shell runtime, trusted frontend access | the target app must already be installed somewhere |

Existing Docker/Compose providers are retained. Installing the baseline is not permission to create a core, replace networking, join a swarm, or deploy a VM. Access and firmware checks can still fail after packages install; failures must remain visible.

The `build-capability-debs.py` builder creates four small dependency-only packages for Ubuntu24.04 amd64. Debian alternatives accept existing Docker CE or Ubuntu Docker packages; the Polari package depends on the Mesh baseline. They have no maintainer scripts. Use `sudo apt install ./<package>.deb` so dependencies are resolved, then the enable command to verify services and membership. They are not full offline dependency archives: network or an appropriately populated local APT repository is needed for missing dependencies.

Hardware Bridge Apps require an app-specific hardware shell, drivers/transports, privileges and demonstrated device access. VPN applications choose different container, kernel and router requirements. Hardware Extensions require their parent app. These types have no universal preparation promise; their per-app requirements and install plan remain authoritative.
