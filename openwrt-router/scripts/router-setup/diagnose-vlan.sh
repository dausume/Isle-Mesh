#!/bin/sh
# Diagnostic script to check VLAN support on OpenWrt router

echo "=== OpenWrt VLAN Diagnostic ==="
echo

echo "1. Checking kernel VLAN support..."
if [ -f /proc/config.gz ]; then
    echo "   Checking kernel config..."
    zcat /proc/config.gz | grep -i "CONFIG_VLAN_8021Q"
elif [ -f /boot/config-$(uname -r) ]; then
    grep -i "CONFIG_VLAN_8021Q" /boot/config-$(uname -r)
else
    echo "   ⚠️  Kernel config not available"
fi
echo

echo "2. Checking if 8021q module exists..."
if find /lib/modules/$(uname -r)/ -name "8021q.ko" 2>/dev/null | grep -q 8021q; then
    echo "   ✓ 8021q module found"
    find /lib/modules/$(uname -r)/ -name "8021q.ko"
else
    echo "   ✗ 8021q module not found (may be built into kernel)"
fi
echo

echo "3. Checking loaded modules..."
if lsmod | grep -q 8021q; then
    echo "   ✓ 8021q module is loaded"
    lsmod | grep 8021q
else
    echo "   ✗ 8021q module not loaded"
fi
echo

echo "4. Checking installed packages..."
echo "   Installed kmod packages:"
opkg list-installed | grep kmod | grep -i vlan
echo

echo "5. Checking available VLAN packages..."
echo "   Available in repos:"
opkg list | grep -E "kmod-8021q|kmod.*vlan"
echo

echo "6. Testing VLAN capability..."
if ip link add link eth0 name eth0.999 type vlan id 999 2>/dev/null; then
    echo "   ✓ VLAN interfaces can be created!"
    ip link delete eth0.999 2>/dev/null
else
    echo "   ✗ Cannot create VLAN interfaces"
fi
echo

echo "7. Checking current network interfaces..."
ip link show | grep -E "^[0-9]+:|@"
echo

echo "8. Checking uci network configuration..."
uci show network | grep -v "\.proto='none'" | head -30
echo

echo "=== Diagnostic Complete ==="
