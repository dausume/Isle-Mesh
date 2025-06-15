# vLan MDNS Node

## Purpose
The vLAN mDNS Node – Application Host Unit

This build represents a generic node in a local virtual LAN (vLAN) that hosts one or more Avahi-advertised services accessible over .local subdomains. These nodes form the service layer of the mDNS-enabled vLAN by exposing secure web apps or backend services.
Purpose

The vlan-mdns-node is designed for:

    Self-hosted applications running on isolated vLANs.

    Broadcasting service availability using mDNS (Multicast DNS) via Avahi

    Enabling HTTPS-secured endpoints (e.g., https://frontend.mesh-app.local)

    Participating in mesh-aware service discovery via .local domains

Key Features

    Runs in a Docker or virtual container

    Publishes mDNS .local subdomains for all hosted apps (e.g., frontend.mesh-app.local)

    Accepts TLS/SSL certs routed via a central vlan-mdns-host proxy or runs its own

    Can serve static React/SPA frontends, APIs, backends, or internal tooling

Example Use Cases

    A frontend container exposing a dashboard at https://dashboard.mesh.local

    A backend container exposing API endpoints at https://api.mesh.local

    A documentation container serving docs at https://docs.mesh.local