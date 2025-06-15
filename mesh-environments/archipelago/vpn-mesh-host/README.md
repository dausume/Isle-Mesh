# VPN-Mesh Host (Gateway & Host)

## Purpose

The VPN Mesh Host acts as a Gateway between your vLAN, the 'isle', and other peer-gateways, members of your
'archipelago'.  Simultaneously it acts as a peer DNS provider to the mesh-network, with other VPN-Mesh hosts on the network.

It utilizes openVPN containers with 802.1Q protocols to bridge multiple vLANs into being treated as a single meshed network,
with e2e encryption of all traffic.

It also allows conversion of isle-mdns websites with .local only domains, to .vpn or .*** domains custom to your mesh-island via dnsmasq,
such that when connected to the mesh-network vpn you gain access to those websites.