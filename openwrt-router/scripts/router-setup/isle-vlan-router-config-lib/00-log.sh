#!/usr/bin/env bash
if [[ -n "${_LOG_SH:-}" ]]; then return; fi; _LOG_SH=1
RED='\033[0;31m'; GREEN='\033[0;32m'; YEL='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info(){ echo -e "${BLU}[INFO]${NC} $*"; }
ok(){   echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn(){ echo -e "${YEL}[WARNING]${NC} $*"; }
err(){  echo -e "${RED}[ERROR]${NC} $*"; }
banner(){ echo; echo -e "${BLU}────────────────────────────────────────────────────────────${NC}"; echo -e "${BLU}  $*${NC}"; echo -e "${BLU}────────────────────────────────────────────────────────────${NC}"; }
