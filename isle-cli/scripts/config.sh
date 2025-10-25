#!/bin/bash

# Isle-Mesh Config Script
# Manages global CLI configuration including current mesh-app project context

set -e

# Config file location
CONFIG_FILE="${HOME}/.isle-config.yml"

# Display help information
print_help() {
  echo "Isle-Mesh Configuration Management"
  echo ""
  echo "Usage: isle config <command> [options]"
  echo ""
  echo "Commands:"
  echo "  set-project <path>    Set the current mesh-app project directory"
  echo "  get-project           Get the current mesh-app project directory"
  echo "  show                  Show all configuration"
  echo "  init <path>           Initialize a new mesh-app project"
  echo "  help                  Show this help message"
  echo ""
  echo "Examples:"
  echo "  isle config set-project ./my-mesh-app"
  echo "  isle config get-project"
  echo "  isle config show"
  echo ""
}

# Initialize config file if it doesn't exist
init_config() {
  if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" <<EOF
# Isle-Mesh CLI Configuration
# This file is automatically managed by the isle CLI

current-project:
  path: null
  name: null

# History of recent projects
recent-projects: []
EOF
    echo "Initialized configuration file: $CONFIG_FILE"
  fi
}

# Set current project path
set_project() {
  local project_path="$1"

  if [ -z "$project_path" ]; then
    echo "Error: Project path required"
    echo "Usage: isle config set-project <path>"
    exit 1
  fi

  # Convert to absolute path
  project_path=$(realpath "$project_path")

  if [ ! -d "$project_path" ]; then
    echo "Error: Directory not found: $project_path"
    exit 1
  fi

  # Check if setup.yml exists in the project
  if [ ! -f "$project_path/setup.yml" ]; then
    echo "Warning: setup.yml not found in $project_path"
    echo "This doesn't appear to be a mesh-app project."
    read -p "Set anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 1
    fi
  fi

  # Get project name from directory
  local project_name=$(basename "$project_path")

  # Initialize config if needed
  init_config

  # Update config file using yq if available, otherwise use sed
  if command -v yq &> /dev/null; then
    yq eval ".\"current-project\".path = \"$project_path\"" -i "$CONFIG_FILE"
    yq eval ".\"current-project\".name = \"$project_name\"" -i "$CONFIG_FILE"
  else
    # Fallback to basic sed replacement
    sed -i "s|^  path:.*|  path: $project_path|" "$CONFIG_FILE"
    sed -i "s|^  name:.*|  name: $project_name|" "$CONFIG_FILE"
  fi

  echo "✓ Current project set to: $project_path"
}

# Get current project path
get_project() {
  init_config

  if command -v yq &> /dev/null; then
    local project_path=$(yq eval '.["current-project"].path' "$CONFIG_FILE")
    if [ "$project_path" = "null" ] || [ -z "$project_path" ]; then
      echo "No project currently set. Use 'isle config set-project <path>' to set one."
      exit 1
    fi
    echo "$project_path"
  else
    echo "Error: yq is required for reading configuration"
    echo "Please install yq: https://github.com/mikefarah/yq"
    exit 1
  fi
}

# Show all configuration
show_config() {
  init_config

  echo "Isle-Mesh Configuration"
  echo "======================="
  echo ""
  cat "$CONFIG_FILE"
}

# Initialize a new mesh-app project
init_project() {
  local project_path="$1"

  if [ -z "$project_path" ]; then
    echo "Error: Project path required"
    echo "Usage: isle config init <path>"
    exit 1
  fi

  # Create directory if it doesn't exist
  mkdir -p "$project_path"
  project_path=$(realpath "$project_path")

  # Create basic setup.yml
  cat > "$project_path/setup.yml" <<EOF
# setup.yml - Environment configuration
current-setup:
  env: dev

environments:
  dev:
    domain: localhost
    expose_ports_on_localhost: true
    projects:
      $(basename "$project_path"): { path: . }

  production:
    domain: mesh-app.local
    projects:
      $(basename "$project_path"): { path: . }
EOF

  # Create directory structure
  mkdir -p "$project_path/config"
  mkdir -p "$project_path/ssl"
  mkdir -p "$project_path/proxy"

  echo "✓ Initialized mesh-app project: $project_path"
  echo "✓ Created setup.yml and directory structure"

  # Set as current project
  set_project "$project_path"
}

# Main command dispatcher
COMMAND=${1:-help}

case $COMMAND in
  set-project)
    set_project "$2"
    ;;
  get-project)
    get_project
    ;;
  show)
    show_config
    ;;
  init)
    init_project "$2"
    ;;
  help|*)
    print_help
    ;;
esac
