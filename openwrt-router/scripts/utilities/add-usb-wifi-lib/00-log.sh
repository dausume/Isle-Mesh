#!/usr/bin/env bash
if [[ -n "${_LOG2_SH:-}" ]]; then return; fi; _LOG2_SH=1
RED='\033[0;31m'; GREEN='\033[0;32m'; YEL='\033[1;33m'; BLU='\033[0;34m'; CYN='\033[0;36m'; NC='\033[0m'
log(){ echo -e "${BLU}[INFO]${NC} $*"; } ok(){ echo -e "${GREEN}[✓]${NC} $*"; }
warn(){ echo -e "${YEL}[WARN]${NC} $*"; } err(){ echo -e "${RED}[✗]${NC} $*"; }
banner(){ echo; echo -e "${CYN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${CYN}  $*${NC}"; echo -e "${CYN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }
