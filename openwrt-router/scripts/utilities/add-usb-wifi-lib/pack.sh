#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/dist"; mkdir -p "$OUT"

bundle() {
  local main="$1" libdir="$2" out="$3"
  {
    cat <<'HDR'
#!/usr/bin/env bash
# Bundled by pack.sh — edit the corresponding *.main.sh and lib/ parts.
set -euo pipefail
BUNDLED_MODE=1
HDR
    echo
    echo "### ===== BEGIN LIBS ====="
    echo
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

bundle "$ROOT/add-ethernet-connection.main.sh" "$ROOT/add-ethernet-lib" "$OUT/add-ethernet-connection.sh"
bundle "$ROOT/add-usb-wifi.main.sh"            "$ROOT/add-usb-wifi-lib" "$OUT/add-usb-wifi.sh"
