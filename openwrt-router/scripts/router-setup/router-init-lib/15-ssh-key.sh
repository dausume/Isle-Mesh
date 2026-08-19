#!/usr/bin/env bash
# BEGIN: 15-ssh-key.sh
if [[ -n "${_SSH_KEY_SH_SOURCED:-}" ]]; then return 0; fi; _SSH_KEY_SH_SOURCED=1

# SSH key storage location
ISLE_SSH_DIR="${ISLE_SSH_DIR:-/etc/isle-mesh/router/ssh}"
ISLE_SSH_KEY="${ISLE_SSH_KEY:-$ISLE_SSH_DIR/isle_router_key}"
ISLE_SSH_PUB="${ISLE_SSH_PUB:-$ISLE_SSH_KEY.pub}"
ISLE_ROUTER_PASS_FILE="${ISLE_SSH_DIR}/.cached_password"

# SSH options to use the dedicated key
ISLE_SSH_OPTS="-i $ISLE_SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Load cached router password (persisted across subprocesses)
if [[ -z "${ROUTER_PASSWORD:-}" ]] && [[ -f "$ISLE_ROUTER_PASS_FILE" ]]; then
  ROUTER_PASSWORD="$(cat "$ISLE_ROUTER_PASS_FILE" 2>/dev/null)"
fi

# Cache the router password so other scripts/subprocesses can reuse it
_cache_router_password() {
  local pass="$1"
  mkdir -p "$(dirname "$ISLE_ROUTER_PASS_FILE")"
  printf '%s' "$pass" > "$ISLE_ROUTER_PASS_FILE"
  chmod 600 "$ISLE_ROUTER_PASS_FILE"
}

# ── Central SSH/SCP helpers (try key, fall back to cached password) ──

# Run an SSH command to the router, handling auth automatically.
# Usage: router_ssh "command to run on router"
router_ssh() {
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local base_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

  # Try key-based auth first (BatchMode prevents password prompt)
  if [[ -f "$ISLE_SSH_KEY" ]]; then
    if ssh -o BatchMode=yes $ISLE_SSH_OPTS "$base_opts" "${router_user}@${router_ip}" "$@" 2>/dev/null; then
      return 0
    fi
  fi

  # Fall back to cached password
  local cached_pass="${ROUTER_PASSWORD:-}"
  if [[ -z "$cached_pass" ]] && [[ -f "$ISLE_ROUTER_PASS_FILE" ]]; then
    cached_pass="$(cat "$ISLE_ROUTER_PASS_FILE" 2>/dev/null)"
  fi

  if [[ -n "$cached_pass" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$cached_pass" ssh $base_opts "${router_user}@${router_ip}" "$@"
    return $?
  fi

  # Last resort: plain ssh (will prompt interactively)
  ssh $base_opts "${router_user}@${router_ip}" "$@"
}

# SCP files to the router, handling auth automatically.
# Usage: router_scp <remote_dest> <local_file1> [local_file2 ...]
# Note: remote dest is FIRST arg so callers can pass expanded globs as remaining args.
router_scp() {
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local remote_path="$1"
  shift
  local base_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

  # -O: use legacy SCP protocol (OpenWRT Dropbear lacks sftp-server)
  local scp_flags="-O"

  # Try key-based auth first
  if [[ -f "$ISLE_SSH_KEY" ]]; then
    if scp $scp_flags -o BatchMode=yes $ISLE_SSH_OPTS $base_opts "$@" "${router_user}@${router_ip}:${remote_path}" 2>/dev/null; then
      return 0
    fi
  fi

  # Fall back to cached password
  local cached_pass="${ROUTER_PASSWORD:-}"
  if [[ -z "$cached_pass" ]] && [[ -f "$ISLE_ROUTER_PASS_FILE" ]]; then
    cached_pass="$(cat "$ISLE_ROUTER_PASS_FILE" 2>/dev/null)"
  fi

  if [[ -n "$cached_pass" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$cached_pass" scp $scp_flags $base_opts "$@" "${router_user}@${router_ip}:${remote_path}"
    return $?
  fi

  # Last resort
  scp $scp_flags $base_opts "$@" "${router_user}@${router_ip}:${remote_path}"
}

# Test if we can reach the router via SSH (non-interactive, no prompts)
router_ssh_test() {
  local router_ip="${ROUTER_IP:-192.168.1.1}"
  local router_user="${ROUTER_USER:-root}"
  local base_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

  # Try key
  if [[ -f "$ISLE_SSH_KEY" ]]; then
    if ssh -o BatchMode=yes $ISLE_SSH_OPTS $base_opts "${router_user}@${router_ip}" "exit" 2>/dev/null; then
      return 0
    fi
  fi

  # Try cached password
  local cached_pass="${ROUTER_PASSWORD:-}"
  if [[ -z "$cached_pass" ]] && [[ -f "$ISLE_ROUTER_PASS_FILE" ]]; then
    cached_pass="$(cat "$ISLE_ROUTER_PASS_FILE" 2>/dev/null)"
  fi

  if [[ -n "$cached_pass" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$cached_pass" ssh $base_opts "${router_user}@${router_ip}" "exit" 2>/dev/null
    return $?
  fi

  return 1
}

# ── Key setup functions ──

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

  # Generate new SSH key (if-wrapped: the packed script runs under
  # set -e, so a bare command + $?-check dies before the check)
  log_info "Generating new SSH key for router communication..."
  if sudo ssh-keygen -t rsa -b 4096 -f "$ISLE_SSH_KEY" -N "" -C "isle-router-key" >/dev/null 2>&1; then
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

  local ssh_base_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

  # Require sshpass for non-interactive password auth
  if ! command -v sshpass >/dev/null 2>&1; then
    log_error "sshpass is required to install SSH key to router"
    log_info "Install with: sudo apt-get install sshpass"
    return 1
  fi

  local router_password="${ROUTER_PASSWORD:-}"
  local authenticated=false

  # Helper: test SSH with a given password (non-interactive)
  _try_ssh_pass() {
    sshpass -p "$1" ssh $ssh_base_opts "${router_user}@${router_ip}" "exit" 2>/dev/null
  }

  # If we have a cached/env password, try it first
  if [[ -n "$router_password" ]]; then
    log_info "Trying cached router password..."
    local max_attempts=10
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
      attempt=$((attempt + 1))
      if _try_ssh_pass "$router_password"; then
        authenticated=true
        break
      fi
      log_info "Waiting for router SSH (attempt $attempt/$max_attempts)..."
      sleep 3
    done
  fi

  # No cached password or it didn't work — prompt the user
  if [[ "$authenticated" != "true" ]]; then
    # Wait for SSH port to be reachable first
    log_info "Waiting for router SSH port..."
    local max_attempts=15
    local attempt=0
    while ! nc -z -w 2 "$router_ip" 22 2>/dev/null; do
      attempt=$((attempt + 1))
      if [[ $attempt -ge $max_attempts ]]; then
        log_error "Router SSH port not reachable after $max_attempts attempts"
        return 1
      fi
      sleep 3
    done

    # Non-interactive first: try the known default router password(s) so a standard install
    # needs no terminal. The isle OpenWRT image ships with a known default root password;
    # once in, the per-install key is installed and all later access is passwordless.
    local _dp
    for _dp in "${ROUTER_DEFAULT_PASSWORD:-root}" "root" ""; do
      if _try_ssh_pass "$_dp"; then
        router_password="$_dp"; authenticated=true
        log_info "Authenticated with default router password"
        break
      fi
    done

    # Fall back to a prompt ONLY if a real terminal is present. An app-invoked / unattended
    # install must never hang on stdin (zero-terminal goal).
    if [[ "$authenticated" != "true" ]]; then
      if [[ -t 0 ]]; then
        echo ""
        echo "  The OpenWRT router requires a password for initial SSH setup."
        echo "  This password will be cached and used to install an SSH key"
        echo "  so that subsequent connections are passwordless."
        echo ""
        echo -n "  Enter root password for router at ${router_ip}: "
        read -s router_password
        echo ""
        if _try_ssh_pass "$router_password"; then
          authenticated=true
        else
          log_error "SSH authentication failed with provided password"
          return 1
        fi
      else
        log_error "Router SSH auth failed (default rejected) and no terminal to prompt."
        log_info "Set ROUTER_PASSWORD env, or ensure the router image ships the default password."
        return 1
      fi
    fi
  fi

  # Cache the working password for other scripts in this session
  _cache_router_password "$router_password"
  ROUTER_PASSWORD="$router_password"
  log_success "SSH connection established"

  # Install the public key to BOTH Dropbear and OpenSSH locations
  # OpenWRT uses Dropbear which reads /etc/dropbear/authorized_keys
  log_info "Installing public key to router..."
  if sshpass -p "$router_password" ssh $ssh_base_opts \
      "${router_user}@${router_ip}" \
      "mkdir -p /etc/dropbear && \
       grep -qxF \"$pub_key\" /etc/dropbear/authorized_keys 2>/dev/null || \
       echo \"$pub_key\" >> /etc/dropbear/authorized_keys && \
       chmod 600 /etc/dropbear/authorized_keys" 2>/dev/null; then
    log_success "SSH key installed to router"
  else
    log_error "Failed to install SSH key to router"
    return 1
  fi

  # Verify key-based SSH works (BatchMode=yes prevents password fallback)
  log_info "Verifying key-based SSH..."
  if ssh -o BatchMode=yes $ISLE_SSH_OPTS -o ConnectTimeout=5 \
          "${router_user}@${router_ip}" "echo 'SSH_KEY_WORKS'" >/dev/null 2>&1; then
    log_success "Key-based SSH working"
  else
    log_warning "Key-based SSH verification failed — will use cached password for subsequent steps"
  fi
}

# Legacy helper functions (kept for compatibility, use router_ssh/router_scp instead)
ssh_router() {
  router_ssh "$@"
}

scp_router() {
  router_scp "$@"
}

# END: 15-ssh-key.sh
