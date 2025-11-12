#!/usr/bin/env bash
# BEGIN: 90-next-steps.sh
if [[ -n "${_NEXT_STEPS_SH_SOURCED:-}" ]]; then return 0; fi; _NEXT_STEPS_SH_SOURCED=1

show_next_steps() {
  log_step "Step 10: Router Initialization Complete!"

  echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║         OpenWRT Router VM Successfully Initialized            ║${NC}"
  echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo
  echo -e "${BLUE}VM Information:${NC}"
  echo -e "  Name:              ${VM_NAME}"
  echo -e "  Status:            $(virsh list | grep -w "$VM_NAME" | awk '{print $3}' || echo "shut off")"
  echo -e "  Memory:            ${MEMORY} MB"
  echo -e "  vCPUs:             ${VCPUS}"
  echo
  echo -e "${BLUE}Network Bridges Created:${NC}"
  echo -e "  ${GREEN}✓${NC} br-mgmt     → eth0 (Management: 192.168.1.x)"
  echo -e "  ${GREEN}✓${NC} isle-br-0   → eth1 (Local isle-agent connectivity)"
  echo
  echo -e "${BLUE}Access Router:${NC}"
  echo -e "  SSH:               ssh root@192.168.1.1"
  echo -e "  Web Interface:     http://192.168.1.1"
  echo
  echo -e "${BLUE}Next Steps:${NC}"
  echo -e "  1) Configure Isle Network on Router:"
  echo -e "     ${GREEN}sudo isle router configure${NC}"
  echo -e "     ${GREEN}→ Sets up VLAN 10 on eth1 with DHCP for isle-agent${NC}"
  echo
  echo -e "  2) Start Local Isle Agent:"
  echo -e "     ${GREEN}cd isle-agent && docker-compose up -d${NC}"
  echo -e "     ${GREEN}→ Agent connects to router via isle-br-0${NC}"
  echo
  echo -e "  3) (Optional) Add External Interfaces:"
  echo -e "     ${GREEN}isle router add-connection${NC}"
  echo -e "     ${GREEN}→ Connect to external isles (isle-br-1, isle-br-2, etc.)${NC}"
  echo
  echo -e "  4) Check VM Status:"
  echo -e "     sudo virsh list"
  echo -e "     sudo virsh console ${VM_NAME}"
  echo
  echo -e "${BLUE}Docs:${NC}"
  echo -e "  - ISP Isolation Security: openwrt-router/docs/ISP-ISOLATION-SECURITY.md"
  echo -e "  - Router Management: isle-cli router --help"
  echo -e "  - Port Detection Service: systemctl status isle-port-detection"
  echo
}
# END: 90-next-steps.sh
