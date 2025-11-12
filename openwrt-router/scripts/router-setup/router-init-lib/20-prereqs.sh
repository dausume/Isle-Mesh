#!/usr/bin/env bash
# BEGIN: 20-prereqs.sh
if [[ -n "${_PREREQS_SH_SOURCED:-}" ]]; then return 0; fi; _PREREQS_SH_SOURCED=1

check_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
  fi
}

_detect_pkg_mgr() {
  command -v apt-get >/dev/null 2>&1 && { echo apt; return; }
  command -v dnf      >/dev/null 2>&1 && { echo dnf; return; }
  command -v yum      >/dev/null 2>&1 && { echo yum; return; }
  command -v pacman   >/dev/null 2>&1 && { echo pacman; return; }
  command -v zypper   >/dev/null 2>&1 && { echo zypper; return; }
  echo unknown
}

_sudo_prefix() {
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then echo ""
  elif command -v sudo >/dev/null 2>&1; then echo sudo
  else echo ""; fi
}

_try_install() {
  local mgr="$1"; shift
  local SUDO; SUDO=$(_sudo_prefix)
  case "$mgr" in
    apt)
      $SUDO apt-get update -y || return 1
      $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" || return 1 ;;
    dnf|yum)
      $SUDO "$mgr" install -y "$@" || return 1 ;;
    pacman)
      $SUDO pacman -Sy --noconfirm "$@" || return 1 ;;
    zypper)
      $SUDO zypper --non-interactive refresh || true
      $SUDO zypper --non-interactive install "$@" || return 1 ;;
    *) return 1 ;;
  esac
}

_start_libvirt_service() {
  local SUDO; SUDO=$(_sudo_prefix)
  if [[ -n "$SUDO" ]]; then
    log_info "📋 You may be prompted for your password to enable libvirt service"
  fi
  if systemctl list-unit-files | grep -q '^libvirtd\.service'; then
    $SUDO systemctl enable --now libvirtd || true
  elif systemctl list-unit-files | grep -q '^libvirt\.service'; then
    $SUDO systemctl enable --now libvirt || true
  fi
  if ! systemctl is-active --quiet libvirtd 2>/dev/null && \
     ! systemctl is-active --quiet libvirt 2>/dev/null; then
    log_warning "libvirt service is not active."
  fi
}

check_prerequisites() {
  log_step "Step 1: Checking Prerequisites"

  local REQUIRED_CMDS=(virsh qemu-img wget ip setfacl getfacl yad sshpass)
  local missing=()

  for cmd in "${REQUIRED_CMDS[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done

  if (( ${#missing[@]} )); then
    log_warning "Missing required commands: ${missing[*]}"
    local mgr; mgr="$(_detect_pkg_mgr)"
    if [[ "$mgr" == "unknown" ]]; then
      log_error "No supported package manager detected."
      log_info "Install manually: qemu-kvm qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients bridge-utils wget acl yad sshpass"
      exit 1
    fi
    log_info "Attempting auto-install via $mgr…"
    log_info "📋 You may be prompted for your password to install required packages"
    if ! _try_install "$mgr" qemu-kvm qemu-system-x86 qemu-utils \
         libvirt-daemon-system libvirt-clients bridge-utils wget acl yad sshpass; then
      log_error "Automatic installation failed."
      [[ "$mgr" =~ dnf|yum ]] && log_info "Tip: enable EPEL if 'yad' is missing: sudo dnf install -y epel-release && sudo dnf install -y yad"
      log_info "Please install packages manually, then re-run."
      exit 1
    fi
  fi

  _start_libvirt_service
  log_success "All prerequisites installed and services checked"
}
# END: 20-prereqs.sh
