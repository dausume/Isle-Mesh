#!/bin/bash

#############################################################################
# Generate Device XML for Libvirt Domain
#
# This script reads port-mapping.conf and generates libvirt XML snippets
# for USB and Ethernet passthrough to the OpenWRT VM.
#
# Usage: ./generate-router-config-xml.sh [options]
#
# Options:
#   -c, --config FILE    Port mapping config file
#   -t, --type TYPE      Device type (usb|ethernet|all)
#   -h, --help           Show this help message
#############################################################################

set -e

# Default configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/config/port-mapping.conf"
DEVICE_TYPE="all"

# Show usage
show_usage() {
    cat << EOF
Generate Device XML for Libvirt

Usage: $0 [options]

Options:
  -c, --config FILE    Port mapping config file
  -t, --type TYPE      Device type (usb|ethernet|all)
  -h, --help           Show this help message

Output: XML snippets for libvirt domain configuration

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            -t|--type)
                DEVICE_TYPE="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                shift
                ;;
        esac
    done
}

# Generate USB passthrough XML
generate_usb_xml() {
    local USB_PORT="$1"  # e.g., "1-1"

    # Try to find the USB device
    local USB_BUS=""
    local USB_DEV=""

    # Parse USB port path to find bus and device
    # This is simplified - in practice we'd need to look up the device
    # For now, generate placeholder that needs vendor/product ID

    cat << EOF
    <!-- USB Device: Port $USB_PORT -->
    <hostdev mode='subsystem' type='usb' managed='yes'>
      <source>
        <!-- Replace VENDOR_ID and PRODUCT_ID with actual values -->
        <!-- Find with: lsusb -->
        <!-- <vendor id='0xVENDOR_ID'/> -->
        <!-- <product id='0xPRODUCT_ID'/> -->

        <!-- OR use bus/device numbers -->
        <address bus='BUS_NUM' device='DEV_NUM'/>
      </source>
    </hostdev>

EOF
}

# Generate USB passthrough by vendor/product
generate_usb_by_id() {
    local VENDOR_ID="$1"
    local PRODUCT_ID="$2"
    local DESCRIPTION="$3"

    cat << EOF
    <!-- USB Device: $DESCRIPTION -->
    <hostdev mode='subsystem' type='usb' managed='yes'>
      <source>
        <vendor id='0x$VENDOR_ID'/>
        <product id='0x$PRODUCT_ID'/>
      </source>
    </hostdev>

EOF
}

# Generate Ethernet macvtap interface
generate_ethernet_macvtap() {
    local INTERFACE="$1"
    local ISLE_NAME="$2"

    cat << EOF
    <!-- Ethernet Passthrough: $INTERFACE for $ISLE_NAME -->
    <interface type='direct'>
      <source dev='$INTERFACE' mode='bridge'/>
      <model type='virtio'/>
    </interface>

EOF
}

# Generate Ethernet bridge interface
generate_ethernet_bridge() {
    local BRIDGE_NAME="$1"
    local ISLE_NAME="$2"

    cat << EOF
    <!-- Ethernet Bridge: $BRIDGE_NAME for $ISLE_NAME -->
    <interface type='bridge'>
      <source bridge='$BRIDGE_NAME'/>
      <model type='virtio'/>
    </interface>

EOF
}

# Main generation
generate_devices() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "<!-- No port mapping configuration found -->" >&2
        return
    fi

    local USB_XML=""
    local ETH_XML=""

    # Read port mappings
    while IFS='=' read -r KEY VALUE; do
        # Skip comments and empty lines
        [[ "$KEY" =~ ^#.*$ ]] && continue
        [[ -z "$KEY" ]] && continue

        # Trim whitespace
        KEY=$(echo "$KEY" | xargs)
        VALUE=$(echo "$VALUE" | xargs)

        # Parse USB mappings
        if [[ "$KEY" =~ ^USB_ ]]; then
            local USB_PORT="${KEY#USB_}"
            IFS=':' read -r ISLE_NAME VLAN_ID SSID PASSWORD <<< "$VALUE"

            if [[ "$DEVICE_TYPE" == "usb" ]] || [[ "$DEVICE_TYPE" == "all" ]]; then
                USB_XML+=$(generate_usb_xml "$USB_PORT")
            fi
        fi

        # Parse Ethernet mappings
        if [[ "$KEY" =~ ^ETH_ ]]; then
            local INTERFACE="${KEY#ETH_}"
            IFS=':' read -r ISLE_NAME VLAN_ID <<< "$VALUE"

            if [[ "$DEVICE_TYPE" == "ethernet" ]] || [[ "$DEVICE_TYPE" == "all" ]]; then
                # Use macvtap for direct interface passthrough
                ETH_XML+=$(generate_ethernet_macvtap "$INTERFACE" "$ISLE_NAME")
            fi
        fi
    done < "$CONFIG_FILE"

    # Output results
    if [[ "$DEVICE_TYPE" == "usb" ]] || [[ "$DEVICE_TYPE" == "all" ]]; then
        if [[ -z "$USB_XML" ]]; then
            echo "    <!-- No USB devices configured -->"
        else
            echo "$USB_XML"
        fi
    fi

    if [[ "$DEVICE_TYPE" == "ethernet" ]] || [[ "$DEVICE_TYPE" == "all" ]]; then
        if [[ -z "$ETH_XML" ]]; then
            echo "    <!-- No Ethernet devices configured -->"
        else
            echo "$ETH_XML"
        fi
    fi
}

# Generate USB device detection helper
generate_usb_detection_script() {
    cat << 'EOF'
#!/bin/bash
# USB Device Detection Helper
# Run this to find vendor/product IDs for your WiFi adapters

echo "USB WiFi Adapters:"
echo "=================="
lsusb | grep -iE 'wireless|wifi|wlan|802.11|ralink|realtek|atheros|mediatek' | while read line; do
    BUS=$(echo "$line" | awk '{print $2}')
    DEV=$(echo "$line" | awk '{print $4}' | tr -d ':')

    echo ""
    echo "Device: $line"
    echo "  Bus: $BUS, Device: $DEV"

    # Get vendor and product IDs
    DETAILS=$(lsusb -v -s $BUS:$DEV 2>/dev/null | grep -E 'idVendor|idProduct' | head -2)
    echo "$DETAILS" | while read detail; do
        echo "  $detail"
    done
done

echo ""
echo "To passthrough a device, add to port-mapping.conf:"
echo "USB_<PORT>=<ISLE>:<VLAN>:<SSID>:<PASSWORD>"
EOF
}

# Main execution
main() {
    parse_args "$@"
    generate_devices
}

# Run main function
main "$@"
