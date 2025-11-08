#!/usr/bin/env bash
# common-log.sh - Unified logging library for Isle Mesh OpenWRT scripts
# Usage: source "$(dirname $0)/../lib/common-log.sh"

if [[ -n "${_COMMON_LOG_SH:-}" ]]; then return 0; fi
_COMMON_LOG_SH=1

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*" >&2
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $*" >&2
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

log_error() {
    echo -e "${RED}[✗]${NC} $*" >&2
}

log_step() {
    echo >&2
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" >&2
    echo -e "${CYAN}  $*${NC}" >&2
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" >&2
}

log_banner() {
    echo >&2
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}" >&2
    echo -e "${CYAN}║  $*${NC}" >&2
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}" >&2
}

# Aliases for shorter names (backwards compatibility)
log() { log_info "$@"; }
ok() { log_success "$@"; }
warn() { log_warning "$@"; }
err() { log_error "$@"; }
info() { log_info "$@"; }
banner() { log_banner "$@"; }
