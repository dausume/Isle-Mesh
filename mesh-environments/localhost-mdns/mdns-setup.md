# Setup /etc/hosts
The /etc/hosts file helps resolvers in browsers locally to resolve
to your localhost, mdns is also necessary in addition to this setup.

Decide on your primary domain for this host (mDNS permits one per device),
then decide on what subdomains you want on the host, you can use as many as you want.

The default domain name is 'mesh-app.local'.

The mock/sample subdomains are 'backend.mesh-app.local', and 'frontend.mesh-app.local'.

For the primary domain and all sub-domains we want we must ensure lines exist in our /etc/hosts,
in our example we should ensure these lines exist:

127.0.0.1   mesh-app.local
127.0.0.1   backend.mesh-app.local
127.0.0.1   frontend.mesh-app.local

# Setup systemd-networkd to split dns between normal dns and mdns
## Ensure Valid resolved.conf -> Should have the following lines in it so it handles normal dns appropriately.
[Resolve]
DNS=75.75.75.75 75.75.76.76
FallbackDNS=1.1.1.1 8.8.8.8
MulticastDNS=yes
Domains=~local
LLMNR=yes
DNSStubListener=yes

## Ensure /etc/systemd/resolved.conf.d/mdns.conf is valid so it can handle mdns from avahi.
[Resolve]
MulticastDNS=yes
LLMNR=no
DNSOverTLS=no

## Ensure the ISP network is configured into systemd at /etc/systemd/network/20-wifi.network
[Match]
Name=wlp2s0

[Network]
DHCP=yes
MulticastDNS=no
DNS=75.75.75.75
FallbackDNS=1.1.1.1


# Setup avahi to run mdns services on system boot (act like a server)
## Set the active mesh configuration file into /etc/mesh-mdns.env
Copy the appropriate environment file to /etc with the exact name mesh-mdns.env
to make it the active configuration file for the mdns service.
## Set the service executable for mdns
Copy the mesh-mdns-broadcast.sh file to the location /usr/local/bin/mesh-mdns-broadcast.sh
## Make the service file in /etc/systemd/system/mesh-mdns.service
[Unit]
Description=Mesh App mDNS Broadcaster
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/mesh-mdns.env
ExecStart=/usr/local/bin/mesh-mdns-broadcast.sh
Restart=always
RestartSec=5
Nice=10

[Install]
WantedBy=multi-user.target