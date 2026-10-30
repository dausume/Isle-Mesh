#!/usr/bin/env python3
"""Collect validated Isle beacons on one Ethernet interface; discovery is not trust."""
import argparse
import ipaddress
import json
import socket
import time


def parse_beacon(packet):
    text = packet.decode('utf-8').strip()
    if len(text) > 2048 or not text.startswith('ISLE_MESH_DISCOVERY|'):
        raise ValueError('Not an Isle beacon')
    fields = dict(part.split('=', 1) for part in text.split('|')[1:])
    name = fields['isle']
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Invalid Isle name')
    vlan = int(fields['vlan'])
    router = ipaddress.IPv4Address(fields['router'])
    subnet = ipaddress.IPv4Network(fields['dhcp'], strict=False)
    if not 1 <= vlan <= 4094 or router not in subnet or router in (subnet.network_address, subnet.broadcast_address):
        raise ValueError('Invalid network')
    return dict(isle_name=name, vlan_id=vlan, router_ip=str(router), dhcp_range=str(subnet))


def collect(interface, timeout, port=7878):
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode()+b'\0')
        sock.bind(('', port))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            sock.settimeout(max(0.01, end-time.monotonic()))
            try:
                packet, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                item = parse_beacon(packet)
                found[(item['router_ip'], item['vlan_id'])] = item
            except (ValueError, KeyError, UnicodeError):
                continue
    return sorted(found.values(), key=lambda row: (row['isle_name'], row['router_ip']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', required=True)
    parser.add_argument('--timeout', type=int, default=35)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error('timeout must be 1–300 seconds')
    try:
        print(json.dumps(collect(args.interface, args.timeout)))
    except OSError as exc:
        parser.exit(1, f'Discovery failed: {exc}\n')
