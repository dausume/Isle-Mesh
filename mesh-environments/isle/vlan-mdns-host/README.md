@mesh-apps/mesh-environments/vlan-mdns-host

The vLAN mDNS Host - Virtual Network Build

The purpose of this virtual network build is to act as a host for your overall vLAN,
centralizing and cataloging the information on your mDNS and providing services to search through
for your mDNS sites (domains and sub-domains).

This is the 'original' or first vLAN you should set up if you intend to scale a vLAN to the size of a small or medium
organization's internal network. If you intend to scale beyond that, you will likely want to scale up to convert your
vLANs into mesh islands via inner-mesh reticulum that use high-speed 4-5G Wi-Fi interconnects for rapid intercommunications
at building or neighborhood scales. Or radio and LoRA app-source and app-state reticulum inter-island communications
which compensate for low speed and security by being fully open source and broadcasting critical apps and data openly
while anonymizing and encrypting critical information and restricting it to LoRA and Wi-Fi interconnects only.

The vLAN host should implement Network Management capabilities and should always act as an intermediary between the vLAN and
any kind of VPN-gateways to the internet.

Optionally it can also be an intermediary to the gateways between your vLAN and inter-mesh communications (LoRA and radio with 3rd parties),
or between your vLAN and mesh-island communications (high-speed Wi-Fi interconnects with reticulum).