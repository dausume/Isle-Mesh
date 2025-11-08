#!/bin/sh
# Initialize Isle Discovery service on OpenWRT
# This script sets up the discovery beacon as a daemon

ISLE_NAME="{{ISLE_NAME}}"
VLAN_ID="{{VLAN_ID}}"
BEACON_SCRIPT="/usr/bin/isle-discovery-beacon"
INIT_SCRIPT="/etc/init.d/isle-discovery"

# Create init.d service file
cat > "${INIT_SCRIPT}" <<'INIT_EOF'
#!/bin/sh /etc/rc.common

START=99
STOP=10

USE_PROCD=1
PROG=/usr/bin/isle-discovery-beacon

start_service() {
    procd_open_instance
    procd_set_param command $PROG
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}

stop_service() {
    killall isle-discovery-beacon 2>/dev/null || true
}
INIT_EOF

chmod +x "${INIT_SCRIPT}"

# Enable service
"${INIT_SCRIPT}" enable

# Start service
"${INIT_SCRIPT}" start

echo "Isle Discovery beacon installed and started"
echo "Service: /etc/init.d/isle-discovery {start|stop|restart|status}"
