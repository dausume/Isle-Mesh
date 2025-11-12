#!/usr/bin/env python3
"""
generate-app-fragment.py

Generates an nginx config fragment for a single mesh-app to be included
in the unified isle-agent configuration.

Usage:
    python3 generate-app-fragment.py \
        --app-name myapp \
        --compose /path/to/docker-compose.yml \
        --domain myapp.local \
        --output /etc/isle-mesh/agent/configs/myapp.conf
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape


def parse_docker_compose(compose_file: Path, script_dir: Path) -> dict:
    """
    Parse docker-compose file using the mesh-proxy parse script.
    Returns service information as a dictionary.
    """
    # Use the parse script from mesh-proxy
    parse_script = script_dir.parent.parent / "mesh-proxy" / "scripts" / "parse-docker-compose.sh"

    if not parse_script.exists():
        print(f"Error: Parse script not found: {parse_script}", file=sys.stderr)
        sys.exit(1)

    try:
        result = subprocess.run(
            [str(parse_script), str(compose_file)],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error parsing docker-compose: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from parse script: {e}", file=sys.stderr)
        print(f"Script output was: {result.stdout}", file=sys.stderr)
        sys.exit(1)


def load_registry(registry_file: Path) -> dict:
    """Load the domain registry."""
    if not registry_file.exists():
        return {"domains": {}, "subdomains": {}, "apps": {}}

    try:
        with open(registry_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load registry: {e}", file=sys.stderr)
        return {"domains": {}, "subdomains": {}, "apps": {}}


def check_conflicts(app_name: str, domain: str, services: list, registry: dict) -> list:
    """
    Check for domain/subdomain conflicts with other apps.
    Returns list of conflict errors.
    """
    conflicts = []

    # Check if domain is already claimed by another app
    if domain in registry.get("domains", {}):
        existing_app = registry["domains"][domain]
        if existing_app != app_name:
            conflicts.append(
                f"Domain '{domain}' is already claimed by app '{existing_app}'"
            )

    # Check if subdomains are already claimed
    for service in services:
        subdomain_fqdn = f"{service['subdomain']}.{domain}"
        if subdomain_fqdn in registry.get("subdomains", {}):
            existing_app = registry["subdomains"][subdomain_fqdn]
            if existing_app != app_name:
                conflicts.append(
                    f"Subdomain '{subdomain_fqdn}' is already claimed by app '{existing_app}'"
                )

    return conflicts


def update_registry(app_name: str, domain: str, services: list, registry_file: Path) -> None:
    """
    Update the domain registry with this app's claims.
    """
    registry = load_registry(registry_file)

    # Register domain
    registry.setdefault("domains", {})[domain] = app_name

    # Register subdomains
    registry.setdefault("subdomains", {})
    for service in services:
        subdomain_fqdn = f"{service['subdomain']}.{domain}"
        registry["subdomains"][subdomain_fqdn] = app_name

    # Register app metadata
    registry.setdefault("apps", {})[app_name] = {
        "domain": domain,
        "services": len(services),
        "subdomains": [f"{svc['subdomain']}.{domain}" for svc in services],
        "updated_at": datetime.now().isoformat()
    }

    # Write registry
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_file, 'w') as f:
        json.dump(registry, f, indent=2)


def build_app_fragment(
    app_name: str,
    services: list,
    base_domain: str,
    base_cert: str,
    base_key: str,
    template_dir: Path,
    segments_dir: Path
) -> str:
    """
    Build nginx config fragment for a single app using Jinja2 templates.
    """
    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader([str(template_dir), str(segments_dir)]),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True
    )

    # Prepare template context
    service_list = []
    for svc in services:
        service_list.append({
            'service_name': svc['name'],
            'service_port': svc['port'],
            'subdomain': svc['subdomain'],
            'mtls': svc.get('mtls', False)
        })

    context = {
        'app_name': app_name,
        'base_domain': base_domain,
        'base_cert': base_cert,
        'base_key': base_key,
        'services': service_list,
        'timestamp': datetime.now().isoformat()
    }

    # Load and render the fragment template
    template = env.get_template('app-fragment.conf.j2')
    return template.render(context)


def main():
    parser = argparse.ArgumentParser(
        description='Generate nginx config fragment for a mesh-app'
    )
    parser.add_argument(
        '--app-name',
        type=str,
        required=True,
        help='Application name (used for namespacing)'
    )
    parser.add_argument(
        '--compose',
        type=Path,
        required=True,
        help='Path to docker-compose.yml file'
    )
    parser.add_argument(
        '--domain',
        type=str,
        required=True,
        help='Base domain (e.g., myapp.local)'
    )
    parser.add_argument(
        '--base-cert',
        type=str,
        default=None,
        help='Base SSL certificate filename (defaults to {domain}.crt)'
    )
    parser.add_argument(
        '--base-key',
        type=str,
        default=None,
        help='Base SSL key filename (defaults to {domain}.key)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        required=True,
        help='Output path for generated config fragment'
    )
    parser.add_argument(
        '--registry',
        type=Path,
        default=Path('/etc/isle-mesh/agent/registry.json'),
        help='Path to domain registry file'
    )
    parser.add_argument(
        '--check-conflicts',
        action='store_true',
        default=True,
        help='Check for domain/subdomain conflicts (default: True)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force generation even if conflicts exist'
    )

    args = parser.parse_args()

    # Set default cert/key names based on domain
    if args.base_cert is None:
        args.base_cert = f"{args.domain}.crt"
    if args.base_key is None:
        args.base_key = f"{args.domain}.key"

    # Determine script and template directories
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    template_dir = project_root / 'templates'
    segments_dir = project_root / 'segments'

    # Verify paths exist
    if not args.compose.exists():
        print(f"Error: Docker compose file not found: {args.compose}", file=sys.stderr)
        sys.exit(1)

    if not template_dir.exists():
        print(f"Error: Template directory not found: {template_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse docker-compose file
    print(f"Parsing docker-compose file: {args.compose}")
    compose_data = parse_docker_compose(args.compose, script_dir)
    services = compose_data.get('services', [])

    # Filter out the proxy service itself
    services = [svc for svc in services if svc['name'] not in ['proxy', 'mesh-proxy', 'isle-agent']]

    if not services:
        print("Error: No services found in docker-compose file", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(services)} services for {args.app_name}:")
    for svc in services:
        mtls_flag = " (mTLS)" if svc.get('mtls') else ""
        print(f"  - {svc['name']}: {svc['subdomain']}.{args.domain}:{svc['port']}{mtls_flag}")

    # Check for conflicts
    if args.check_conflicts:
        print("\nChecking for domain conflicts...")
        registry = load_registry(args.registry)
        conflicts = check_conflicts(args.app_name, args.domain, services, registry)

        if conflicts:
            print("\nCONFLICT ERRORS:", file=sys.stderr)
            for conflict in conflicts:
                print(f"  ✗ {conflict}", file=sys.stderr)

            if not args.force:
                print("\nUse --force to override conflict checking", file=sys.stderr)
                sys.exit(1)
            else:
                print("\nWarning: Proceeding with --force flag")

    # Build nginx fragment
    print(f"\nGenerating nginx config fragment for '{args.app_name}'...")
    fragment = build_app_fragment(
        app_name=args.app_name,
        services=services,
        base_domain=args.domain,
        base_cert=args.base_cert,
        base_key=args.base_key,
        template_dir=template_dir,
        segments_dir=segments_dir
    )

    # Write output file
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(fragment)
    print(f"✓ Fragment written to: {args.output}")

    # Update registry
    print(f"Updating registry: {args.registry}")
    update_registry(args.app_name, args.domain, services, args.registry)
    print(f"✓ Registry updated")

    print(f"\n✓ Config fragment for '{args.app_name}' generated successfully")
    print(f"  Domain: {args.domain}")
    print(f"  Services: {len(services)}")
    print(f"\nTo activate, reload the isle-agent:")
    print(f"  sudo isle agent reload")


if __name__ == '__main__':
    main()
