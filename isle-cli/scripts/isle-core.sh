#!/bin/bash

# Isle-Mesh Core Operations
# Simplified commands for managing mesh-app projects

set -e

# Config file location
CONFIG_FILE="${HOME}/.isle-config.yml"

# Get current project directory
get_current_project() {
  if [ ! -f "$CONFIG_FILE" ]; then
    return 1
  fi

  if command -v yq &> /dev/null; then
    local project_path=$(yq eval '.["current-project"].path' "$CONFIG_FILE" 2>/dev/null)
    if [ "$project_path" = "null" ] || [ -z "$project_path" ]; then
      return 1
    fi
    echo "$project_path"
    return 0
  else
    return 1
  fi
}

# Initialize config if it doesn't exist
init_config() {
  if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" <<EOF
# Isle-Mesh CLI Configuration
current-project:
  path: null
  name: null
recent-projects: []
EOF
  fi
}

# Set current project
set_current_project() {
  local project_path="$1"
  project_path=$(realpath "$project_path")
  local project_name=$(basename "$project_path")

  init_config

  if command -v yq &> /dev/null; then
    yq eval ".\"current-project\".path = \"$project_path\"" -i "$CONFIG_FILE"
    yq eval ".\"current-project\".name = \"$project_name\"" -i "$CONFIG_FILE"
  else
    sed -i "s|^  path:.*|  path: $project_path|" "$CONFIG_FILE"
    sed -i "s|^  name:.*|  name: $project_name|" "$CONFIG_FILE"
  fi
}

# isle init - Initialize a mesh-app project
cmd_init() {
  local compose_file=""
  local output_dir="."
  local domain="mesh-app.local"
  local project_name=""
  local auto_detect=true

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case $1 in
      -f|--file)
        compose_file="$2"
        auto_detect=false
        shift 2
        ;;
      -o|--output)
        output_dir="$2"
        shift 2
        ;;
      -d|--domain)
        domain="$2"
        shift 2
        ;;
      -n|--name)
        project_name="$2"
        shift 2
        ;;
      -h|--help)
        echo "Usage: isle init [options]"
        echo ""
        echo "Initialize a new Isle-Mesh project from docker-compose or from scratch"
        echo ""
        echo "When run without -f flag, isle init will:"
        echo "  • Auto-detect docker-compose files in current directory"
        echo "  • If one file found, use it automatically"
        echo "  • If multiple found, prompt you to choose"
        echo "  • If none found, create a new project from scratch"
        echo ""
        echo "Options:"
        echo "  -f, --file FILE      Docker compose file to convert (optional)"
        echo "  -o, --output DIR     Output directory (default: current directory)"
        echo "  -d, --domain DOMAIN  Base domain (default: mesh-app.local)"
        echo "  -n, --name NAME      Project name (default: auto-detected)"
        echo "  -h, --help           Show this help"
        echo ""
        echo "Examples:"
        echo "  isle init                                    # Auto-detect or create new"
        echo "  isle init -f docker-compose.yml              # Convert specific file"
        echo "  isle init -f app.yml -d myapp.local -o ./out # Full customization"
        exit 0
        ;;
      *)
        compose_file="$1"
        auto_detect=false
        shift
        ;;
    esac
  done

  # Auto-detect docker-compose files if no file specified
  if [ "$auto_detect" = true ] && [ -z "$compose_file" ]; then
    # Find all docker-compose files, excluding mesh-app files
    local compose_files=()
    while IFS= read -r file; do
      compose_files+=("$file")
    done < <(find . -maxdepth 1 -type f \( -name "docker-compose*.yml" -o -name "docker-compose*.yaml" -o -name "compose*.yml" -o -name "compose*.yaml" \) ! -name "*mesh-app*" ! -name "*setup*" 2>/dev/null | sort)

    local num_files=${#compose_files[@]}

    if [ $num_files -eq 0 ]; then
      echo "No docker-compose files found in current directory."
      echo "Creating a new mesh-app project from scratch..."
      echo ""
    elif [ $num_files -eq 1 ]; then
      compose_file="${compose_files[0]}"
      echo "Found docker-compose file: $compose_file"
      echo "Converting to mesh-app..."
      echo ""
    else
      echo "Found multiple docker-compose files:"
      echo ""
      for i in "${!compose_files[@]}"; do
        echo "  $((i+1)). ${compose_files[$i]}"
      done
      echo "  $((num_files+1)). Create new project from scratch (don't use any file)"
      echo ""

      local choice=""
      while true; do
        read -p "Select file to convert (1-$((num_files+1))): " choice

        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $((num_files+1)) ]; then
          if [ "$choice" -eq $((num_files+1)) ]; then
            echo ""
            echo "Creating new project from scratch..."
            compose_file=""
            break
          else
            compose_file="${compose_files[$((choice-1))]}"
            echo ""
            echo "Converting $compose_file to mesh-app..."
            break
          fi
        else
          echo "Invalid choice. Please enter a number between 1 and $((num_files+1))."
        fi
      done
      echo ""
    fi
  fi

  # Get script directory
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [ ! -z "$compose_file" ]; then
    # Convert existing docker-compose file
    echo "Initializing mesh-app from $compose_file..."
    bash "$SCRIPT_DIR/scaffold.sh" "$compose_file" -o "$output_dir" -d "$domain" ${project_name:+-n "$project_name"}
  else
    # Create new mesh-app from scratch
    echo "Creating new mesh-app project..."

    # If output_dir is ".", use the current working directory
    if [ "$output_dir" = "." ]; then
      output_dir="$PWD"
    else
      output_dir=$(realpath "$output_dir")
    fi
    mkdir -p "$output_dir"

    if [ -z "$project_name" ]; then
      project_name=$(basename "$output_dir")
    fi

    # Create setup.yml
    cat > "$output_dir/setup.yml" <<EOF
# setup.yml - Environment configuration for $project_name
current-setup:
  env: dev

environments:
  dev:
    domain: localhost
    expose_ports_on_localhost: true
    projects:
      $project_name: { path: . }

  production:
    domain: $domain
    projects:
      $project_name: { path: . }
EOF

    # Create directory structure
    mkdir -p "$output_dir/config"
    mkdir -p "$output_dir/ssl"
    mkdir -p "$output_dir/proxy"

    # Create basic docker-compose.mesh-app.yml template
    cat > "$output_dir/docker-compose.mesh-app.yml" <<EOF
version: '3.8'

networks:
  ${project_name}_meshnet:
    driver: bridge

services:
  # Add your services here
  # Example:
  # myservice:
  #   image: nginx:alpine
  #   networks:
  #     - ${project_name}_meshnet
  #   expose:
  #     - "80"
EOF

    # Create isle-mesh.yml
    cat > "$output_dir/isle-mesh.yml" <<EOF
# isle-mesh.yml - Mesh network configuration
mesh:
  name: $project_name
  domain: $domain
  version: "1.0.0"

network:
  name: ${project_name}_meshnet
  driver: bridge

services: {}
EOF

    echo "✓ Created mesh-app project: $output_dir"
    echo "✓ Created setup.yml"
    echo "✓ Created docker-compose.mesh-app.yml"
    echo "✓ Created isle-mesh.yml"
  fi

  # Set as current project
  set_current_project "$output_dir"
  echo "✓ Set as current project"
}

# isle up - Start mesh-app services
cmd_up() {
  local project_dir=$(get_current_project)
  local build_flag=""
  local detach_flag="-d"
  local extra_args=()

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case $1 in
      --build)
        build_flag="--build"
        shift
        ;;
      --no-detach)
        detach_flag=""
        shift
        ;;
      -p|--project)
        project_dir="$2"
        shift 2
        ;;
      *)
        extra_args+=("$1")
        shift
        ;;
    esac
  done

  if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
    echo "Error: No current project set. Use 'isle init' or 'isle config set-project <path>'"
    exit 1
  fi

  cd "$project_dir"

  if [ -f "docker-compose.mesh-app.yml" ]; then
    echo "Starting mesh-app services in $project_dir..."
    docker compose -f docker-compose.mesh-app.yml up $detach_flag $build_flag "${extra_args[@]}"
  elif [ -f "docker-compose.yml" ]; then
    echo "Starting services from docker-compose.yml in $project_dir..."
    docker compose up $detach_flag $build_flag "${extra_args[@]}"
  else
    echo "Error: No docker-compose file found in $project_dir"
    exit 1
  fi
}

# isle down - Stop mesh-app services
cmd_down() {
  local project_dir=$(get_current_project)
  local remove_volumes=""
  local extra_args=()

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case $1 in
      -v|--volumes)
        remove_volumes="-v"
        shift
        ;;
      -p|--project)
        project_dir="$2"
        shift 2
        ;;
      *)
        extra_args+=("$1")
        shift
        ;;
    esac
  done

  if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
    echo "Error: No current project set"
    exit 1
  fi

  cd "$project_dir"

  if [ -f "docker-compose.mesh-app.yml" ]; then
    echo "Stopping mesh-app services..."
    docker compose -f docker-compose.mesh-app.yml down $remove_volumes "${extra_args[@]}"
  elif [ -f "docker-compose.yml" ]; then
    echo "Stopping services..."
    docker compose down $remove_volumes "${extra_args[@]}"
  else
    echo "Error: No docker-compose file found"
    exit 1
  fi
}

# isle prune - Clean up all mesh resources
cmd_prune() {
  local force=false

  while [[ $# -gt 0 ]]; do
    case $1 in
      -f|--force)
        force=true
        shift
        ;;
      -h|--help)
        echo "Usage: isle prune [options]"
        echo ""
        echo "Clean up all Isle-Mesh Docker resources"
        echo ""
        echo "Options:"
        echo "  -f, --force    Skip confirmation prompt"
        echo "  -h, --help     Show this help"
        exit 0
        ;;
      *)
        shift
        ;;
    esac
  done

  echo "╔═══════════════════════════════════════════════════════════════╗"
  echo "║                   Isle-Mesh Prune                             ║"
  echo "╚═══════════════════════════════════════════════════════════════╝"
  echo ""
  echo "This will remove:"
  echo "  • All stopped Isle-Mesh containers"
  echo "  • All Isle-Mesh networks"
  echo "  • All dangling images from Isle-Mesh projects"
  echo ""

  if [ "$force" = false ]; then
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "Cancelled."
      exit 0
    fi
  fi

  echo "Pruning Isle-Mesh resources..."

  # Stop all mesh-related containers
  echo "• Stopping mesh containers..."
  docker ps -a --filter "name=mesh" --format "{{.ID}}" | xargs -r docker stop 2>/dev/null || true
  docker ps -a --filter "name=mesh" --format "{{.ID}}" | xargs -r docker rm 2>/dev/null || true

  # Remove mesh networks
  echo "• Removing mesh networks..."
  docker network ls --filter "name=meshnet" --format "{{.ID}}" | xargs -r docker network rm 2>/dev/null || true

  # Prune system
  echo "• Pruning Docker system..."
  docker system prune -f

  echo "✓ Prune complete!"
}

# isle logs - View logs for mesh-app
cmd_logs() {
  local project_dir=$(get_current_project)
  local follow_flag="-f"
  local service=""
  local extra_args=()

  while [[ $# -gt 0 ]]; do
    case $1 in
      --no-follow)
        follow_flag=""
        shift
        ;;
      -p|--project)
        project_dir="$2"
        shift 2
        ;;
      *)
        service="$1"
        extra_args+=("$1")
        shift
        ;;
    esac
  done

  if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
    echo "Error: No current project set"
    exit 1
  fi

  cd "$project_dir"

  if [ -f "docker-compose.mesh-app.yml" ]; then
    docker compose -f docker-compose.mesh-app.yml logs $follow_flag "${extra_args[@]}"
  elif [ -f "docker-compose.yml" ]; then
    docker compose logs $follow_flag "${extra_args[@]}"
  else
    echo "Error: No docker-compose file found"
    exit 1
  fi
}

# isle ps - Show running services
cmd_ps() {
  local project_dir=$(get_current_project)

  if [ -z "$project_dir" ] || [ ! -d "$project_dir" ]; then
    echo "Error: No current project set"
    exit 1
  fi

  cd "$project_dir"

  if [ -f "docker-compose.mesh-app.yml" ]; then
    docker compose -f docker-compose.mesh-app.yml ps "$@"
  elif [ -f "docker-compose.yml" ]; then
    docker compose ps "$@"
  else
    echo "Error: No docker-compose file found"
    exit 1
  fi
}

# Main command dispatcher
COMMAND=${1:-help}
shift || true

case $COMMAND in
  init)
    cmd_init "$@"
    ;;
  up)
    cmd_up "$@"
    ;;
  down)
    cmd_down "$@"
    ;;
  prune)
    cmd_prune "$@"
    ;;
  logs)
    cmd_logs "$@"
    ;;
  ps)
    cmd_ps "$@"
    ;;
  *)
    echo "Error: Unknown command '$COMMAND'"
    echo "Use 'isle help' to see available commands"
    exit 1
    ;;
esac
