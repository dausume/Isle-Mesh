The vLAN mDNS Client – Lightweight Discovery Node

This build represents a client or user device that wants to access .local services available in the vLAN via mDNS resolution. It is configured to:

    Discover services over mDNS (typically via Avahi)

    Route .local requests to the proper containers inside the vLAN

    Trust certificates and subdomain structures managed by the vlan-mdns-host or other nodes

Purpose

    Automatically detect and connect to services hosted by vlan-mdns-node or vlan-mdns-host

    Provide human-usable navigation via .local addresses

    Route traffic securely (via HTTPS) if certs are trusted/imported

Key Features

    Uses Avahi or compatible mDNS client for name resolution

    Lightweight container or VM that can run on laptops, desktops, or embedded systems

    Compatible with:

        Firefox/Chromium (for browsing apps like https://site.mesh.local)

        CLI tools for curl, wget, nmap, etc.

    Optionally includes browser-trustable CA (e.g. from step-ca) to verify .local HTTPS certs

Example Use Cases

    Developers using a secure .local dev environment

    Internal tools accessed via laptops in a secure mesh network

    Education/training labs accessing common mesh services with no internet access