#!/usr/bin/env bash
# template-engine.sh - Simple template variable substitution engine
# Usage: source "$(dirname $0)/../lib/template-engine.sh"

if [[ -n "${_TEMPLATE_ENGINE_SH:-}" ]]; then return 0; fi
_TEMPLATE_ENGINE_SH=1

# Source logging if not already loaded
if [[ -z "${_COMMON_LOG_SH:-}" ]]; then
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    source "$SCRIPT_DIR/common-log.sh"
fi

# Find template directory relative to scripts/
find_template_dir() {
    local script_dir="${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
    # Go up from scripts/lib to openwrt-router, then to templates
    echo "$(cd "$script_dir/../.." && pwd)/templates"
}

TEMPLATE_DIR="${TEMPLATE_DIR:-$(find_template_dir)}"

# Apply template with variable substitution
# Usage: apply_template <template_file> <output_file> [var1=value1 var2=value2 ...]
apply_template() {
    local template_file="$1"
    local output_file="$2"
    shift 2

    if [[ ! -f "$template_file" ]]; then
        log_error "Template not found: $template_file"
        return 1
    fi

    local content
    content=$(< "$template_file")

    # Substitute variables in format {{VAR_NAME}}
    for var in "$@"; do
        local key="${var%%=*}"
        local value="${var#*=}"
        # Escape special characters in value for sed
        value=$(echo "$value" | sed 's/[&/\]/\\&/g')
        content=$(echo "$content" | sed "s/{{${key}}}/${value}/g")
    done

    echo "$content" > "$output_file"
    log_info "Applied template: $(basename "$template_file") -> $(basename "$output_file")"
}

# Apply template and output to stdout
# Usage: render_template <template_file> [var1=value1 var2=value2 ...]
render_template() {
    local template_file="$1"
    shift

    if [[ ! -f "$template_file" ]]; then
        log_error "Template not found: $template_file"
        return 1
    fi

    local content
    content=$(< "$template_file")

    # Substitute variables
    for var in "$@"; do
        local key="${var%%=*}"
        local value="${var#*=}"
        value=$(echo "$value" | sed 's/[&/\]/\\&/g')
        content=$(echo "$content" | sed "s/{{${key}}}/${value}/g")
    done

    echo "$content"
}

# Get template path helper
get_template() {
    local template_name="$1"
    echo "${TEMPLATE_DIR}/${template_name}"
}
