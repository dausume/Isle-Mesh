#!/usr/bin/env bash
# BEGIN: 60-nextsteps.sh
if [[ -n "${_NEXT_SH_SOURCED:-}" ]]; then return 0; fi; _NEXT_SH_SOURCED=1

show_next_steps() {
  log_step "Router Initialization Complete!"

  echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║         OpenWRT Router VM Successfully Initialized            ║${NC}"
  echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo
  echo -e "${BLUE}VM Information:${NC}"
  echo -e "  Name:              ${VM_NAME}"
  echo -e "  Status:            $(virsh list | grep -w "$VM_NAME" | awk '{print $3}' || echo "shut off")"
  echo -e "  Memory:            ${MEMORY} MB"
  echo -e "  vCPUs:             ${VCPUS}"
  echo -e "  Network:           ${YELLOW}NO INTERFACES (dynamic management)${NC}"
  echo
  echo -e "${BLUE}Important Notes:${NC}"
  echo -e "  ${YELLOW}⚠${NC}  The router has NO network interfaces yet!"
  echo -e "  ${YELLOW}⚠${NC}  Interfaces are assigned dynamically for security"
  echo
  echo -e "${BLUE}Next Steps:${NC}"
  echo -e "  1) Enable Port Detection Service (Recommended):"
  echo -e "     sudo systemctl enable isle-port-detection && sudo systemctl start isle-port-detection"
  echo -e "     ${GREEN}→ Automatically prompts when new USB/Ethernet detected${NC}"
  echo
  echo -e "  2) Manual Port Assignment:"
  echo -e "     isle-cli router add-connection"
  echo -e "     ${GREEN}→ Interactively detect and assign ports to router${NC}"
  echo
  echo -e "  3) Check VM Status:"
  echo -e "     sudo virsh list"
  echo -e "     sudo virsh console ${VM_NAME}"
  echo
  echo -e "  4) View VM Configuration:"
  echo -e "     sudo virsh dumpxml ${VM_NAME}"
  echo
  echo -e "${BLUE}Docs:${NC}"
  echo -e "  - ISP Isolation Security: openwrt-router/docs/ISP-ISOLATION-SECURITY.md"
  echo -e "  - Router Management: isle-cli router --help"
  echo -e "  - Port Detection Service: systemctl status isle-port-detection"
  echo
}
# END: 60-nextsteps.sh
