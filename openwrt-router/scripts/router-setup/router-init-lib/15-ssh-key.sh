#!/usr/bin/env bash
# BEGIN: 15-ssh-key.sh
if [[ -n "${_SSH_KEY_SH_SOURCED:-}" ]]; then return 0; fi; _SSH_KEY_SH_SOURCED=1

# SSH key storage location
ISLE_SSH_DIR="${ISLE_SSH_DIR:-/etc/isle-mesh/router/ssh}"
ISLE_SSH_KEY="${ISLE_SSH_KEY:-$ISLE_SSH_DIR/isle_router_key}"
ISLE_SSH_PUB="${ISLE_SSH_PUB:-$ISLE_SSH_KEY.pub}"

# SSH options to use the dedicated key
ISLE_SSH_OPTS="-i $ISLE_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

setup_router_ssh_key() {
  log_step "Setting Up Dedicated SSH Key for Router Communication"

  # Create SSH directory if it doesn't exist
  if [[ ! -d "$ISLE_SSH_DIR" ]]; then
    log_info "Creating SSH key directory: $ISLE_SSH_DIR"
    sudo mkdir -p "$ISLE_SSH_DIR"
    sudo chmod 700 "$ISLE_SSH_DIR"
  fi

  # Check if key already exists
  if [[ -f "$ISLE_SSH_KEY" && -f "$ISLE_SSH_PUB" ]]; then
    log_success "SSH key already exists: $ISLE_SSH_KEY"

    # Verify key is valid
    if ssh-keygen -l -f "$ISLE_SSH_KEY" >/dev/null 2>&1; then
      log_success "Existing SSH key is valid"
      return 0
    else
      log_warning "Existing SSH key is invalid, regenerating..."
      sudo rm -f "$ISLE_SSH_KEY" "$ISLE_SSH_PUB"
    fi
  fi

  # Generate new SSH key
  log_info "Generating new SSH key for router communication..."
  sudo ssh-keygen -t rsa -b 4096 -f "$ISLE_SSH_KEY" -N "" -C "isle-router-key" >/dev/null 2>&1

  if [[ $? -eq 0 ]]; then
    log_success "SSH key generated: $ISLE_SSH_KEY"

    # Set proper permissions
    sudo chmod 600 "$ISLE_SSH_KEY"
    sudo chmod 644 "$ISLE_SSH_PUB"

    log_info "Public key fingerprint:"
    ssh-keygen -l -f "$ISLE_SSH_PUB" 2>/dev/null | sed 's/^/  /'
  else
    log_error "Failed to generate SSH key"
    exit 1
  fi
}

install_ssh_key_to_router() {
  local router_ip="${1:-192.168.1.1}"
  local router_user="${2:-root}"

  log_step "Installing SSH Key to OpenWRT Router"

  # Check if key exists
  if [[ ! -f "$ISLE_SSH_PUB" ]]; then
    log_error "SSH public key not found: $ISLE_SSH_PUB"
    log_info "Run setup_router_ssh_key first"
    return 1
  fi

  log_info "Router: ${router_user}@${router_ip}"

  # Read the public key
  local pub_key
  pub_key=$(sudo cat "$ISLE_SSH_PUB")

  # First, test if we can connect (will prompt for password on first connection)
  log_info "Testing initial SSH connection (may prompt for password)..."
  if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
          "${router_user}@${router_ip}" "exit" 2>/dev/null; then
    log_warning "Cannot connect to router yet, it may still be booting"
    log_info "Waiting 10 seconds for router to be ready..."
    sleep 10
  fi

  # Install the key to router's authorized_keys
  log_info "Installing public key to router..."
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "${router_user}@${router_ip}" \
      "mkdir -p /root/.ssh && chmod 700 /root/.ssh && \
       grep -qxF \"$pub_key\" /root/.ssh/authorized_keys 2>/dev/null || \
       echo \"$pub_key\" >> /root/.ssh/authorized_keys && \
       chmod 600 /root/.ssh/authorized_keys" 2>/dev/null

  if [[ $? -eq 0 ]]; then
    log_success "SSH key installed to router"
  else
    log_warning "Failed to install SSH key (may need to be done manually)"
    return 1
  fi

  # Test passwordless connection with the key
  log_info "Testing passwordless SSH connection..."
  if sudo ssh $ISLE_SSH_OPTS -o ConnectTimeout=5 \
          "${router_user}@${router_ip}" "echo 'SSH_KEY_WORKS'" >/dev/null 2>&1; then
    log_success "Passwordless SSH working with dedicated key"
  else
    log_warning "Passwordless SSH test failed - key may need time to propagate"
  fi
}

# Helper function to SSH to router using the dedicated key
ssh_router() {
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"

  sudo ssh $ISLE_SSH_OPTS "${router_user}@${router_ip}" "$@"
}

# Helper function to SCP to router using the dedicated key
scp_router() {
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local local_file="$1"
  local remote_path="$2"

  sudo scp $ISLE_SSH_OPTS "$local_file" "${router_user}@${router_ip}:${remote_path}"
}

# END: 15-ssh-key.sh
