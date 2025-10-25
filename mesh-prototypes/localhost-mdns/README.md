# Localhost MDNS environment


## Purpose

The localhost-mdns environment is for a combination of test and dev environments and simple self-hosting
where you just want to run an app-suite on your local computer via self-hosting.

The localhost-mdns setup does not involve a vLAN, just normal docker comose setup with mDNS setup that is used only for your local
computer, and not detectable by other computers on your network or vLAN.  The nginx proxy does not permit any traffic except for localhost
traffic on your server, so no one can try and hack their way in even if they know your ip and are on your network.


## Generating SSL certs for localhost-only mdns

Command to generate ssh certs from this directory:

    {use generate mesh ssl sh as a command file} {provide a configuration file} {optional - provide path to the Base Directory, defaults to pwd} {optional - location of generate-mesh-ssl directory}

    ../../ssl/generate_mesh_ssl.sh ./lh-mdns.env.conf ./

## Sample Structure for a full-stack localhost-mdns App.

We design a sample docker-compose structure with the following file structure:

    @localhost-mdns/
        backend/
            backend-subdomain-mesh-app.html
            Dockerfile.lh-mdns.backend
        frontend/
            Dockerfile.lh-mdns.frontend
            frontend-subdomain-mesh-app.html
        mdns/
            Dockerfile.lh-mdns.mdns
        proxy/
            Dockerfile.lh-mdns.mesh-proxy
            lh-mdns.proxy.conf
        ssl/
            certs/
                backend.mesh-app.crt
                mesh-app.crt
            keys/
                backend.mesh-app.key
                mesh-app.key
            stores/
                backend.keys.pki
                proxy.keys.pki
                backend.trust.pki
                proxy.trust.pki
        docker-compose.lh-mdns.yml
        lh-mdns.env.conf
        README.md

## 

    At a lower level, since it is used across multiple environments for sample-apps we also have the generate-mesh-ssl shells.

    @mdns-webapp-setup/
        mesh-prototypes/
            localhost-mdns/
            ...other-environments/
        public-static-data/
            robots.txt
            well-known.json
        ssl/
            certs/
                backend.mesh-app.crt
                mesh-app.crt
            keys/
                backend.mesh-app.key
                mesh-app.key
            stores/
                backend.keys.pki
                proxy.keys.pki
                backend.trust.pki
                proxy.trust.pki
            generate_mesh_ssl.sh
            generate_ssl_config.sh
            generate_ssl.sh

    In our containers, each should be allocated only the ssl files that they have need-to-know for.
    All containers use specific Dockerfiles, and specific entrypoint.sh files for them, so we can
    dynamically define configuration and issue needed commands post-startup. 

We initially structure the sample version of the app so a user can come in and run it, to see that it functions.



----------------------------------------------------------------------------------------------------------

Overview

The localhost-mdns environment is designed for:

    Securely self-hosting applications only on your own computer

    Creating a loopback-only HTTPS mDNS proxy for local development

    Running containerized app suites with minimal configuration

    Simulating .local subdomain behavior without exposing services to your LAN or the public internet

🔒 Trust Model

    No VLAN required — designed for local dev without complex networking.

    mDNS-Only Access — local .local subdomains simulate service-discovery.

    Default-Deny Proxy Rules — blocks all traffic except localhost.

    Zero External Discovery Risk — even on public Wi-Fi or shared LANs.

This setup is ideal for test/development environments, single-user app stacks, or isolated demonstrations.

🔒 Security-First by Design

    All traffic is restricted to localhost (127.0.0.1) — no ports are opened to your LAN or WAN

    Even if someone knows your device’s IP and is on your local network, they cannot access your services

    HTTPS via local self-signed or automated certificates (e.g. step-ca) ensures encryption is always used

    mDNS advertisements are limited to the local machine — not broadcasted to the LAN

🧱 Architecture

This environment includes:

    Docker Compose to orchestrate services (e.g. frontend, backend, auth, etc.)

    Nginx acting as a secure reverse proxy for .local domains

    Avahi or another mDNS publisher (if needed) to simulate .local routing for your apps

    Optional step-ca or SSL tooling to automate HTTPS certificate management

🧭 Example mDNS Domains
Subdomain	Description
https://frontend.mesh-app.local	Your main SPA or dashboard
https://backend.mesh-app.local	API service

These domains are resolved only by your own computer using loopback mDNS and Nginx.