#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/dist"; mkdir -p "$OUT"

bundle() {
  local main="$1" libdir="$2" out="$3"
  {
    cat <<'HDR'
#!/usr/bin/env bash
# Bundled by pack.sh — edit *.main.sh and isle-vlan-router-lib/* for source.
set -euo pipefail
BUNDLED_MODE=1
HDR
    echo
    echo "### ===== BEGIN LIBS ====="
    for f in "$libdir"/[0-9][0-9]-*.sh; do
      echo "# --- $(basename "$f") ---"
      cat "$f"
      echo
    done
    echo "### ===== END LIBS ====="
    echo
    echo "# --- $(basename "$main") ---"
    cat "$main"
  } > "$out"
  chmod +x "$out"
  echo "Built: $out"
}

bundle "$ROOT/isle-vlan-router-config.main.sh" \
       "$ROOT/isle-vlan-router-lib" \
       "$OUT/isle-vlan-router-config.sh"
