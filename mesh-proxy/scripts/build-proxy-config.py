#!/usr/bin/env python3
"""
build-proxy-config.py

Builds nginx mesh-proxy configuration dynamically from docker-compose.yml
using modular Jinja2 templates via embed-jinja.

Usage:
    python3 build-proxy-config.py \
        --compose docker-compose.lh-mdns.yml \
        --domain mesh-app.local \
        --base-cert mesh-app.crt \
        --base-key mesh-app.key \
        --output output/nginx-mesh-proxy.conf
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape


def parse_docker_compose(compose_file: Path, script_dir: Path) -> dict:
    """
    Parse docker-compose file using the shell script.
    Returns service information as a dictionary.
    """
    parse_script = script_dir / "parse-docker-compose.sh"

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


def build_nginx_config(
    services: list,
    base_domain: str,
    base_cert: str,
    base_key: str,
    template_dir: Path,
    segments_dir: Path
) -> str:
    """
    Build nginx configuration using Jinja2 templates.
    """
    # Set up Jinja2 environment with both template and segments directories
    env = Environment(
        loader=FileSystemLoader([str(template_dir), str(segments_dir.parent)]),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True
    )

    # Prepare template context
    subdomains = [svc['subdomain'] for svc in services]

    # Convert service list to include all needed fields
    service_list = []
    for svc in services:
        service_list.append({
            'service_name': svc['name'],
            'service_port': svc['port'],
            'name': svc['name'],
            'subdomain': svc['subdomain'],
            'mtls': svc.get('mtls', False)
        })

    context = {
        'base_domain': base_domain,
        'base_cert': base_cert,
        'base_key': base_key,
        'services': service_list,
        'subdomains': subdomains
    }

    # Load and render the main template
    template = env.get_template('nginx-mesh-proxy.conf.j2')
    return template.render(context)


def main():
    parser = argparse.ArgumentParser(
        description='Build nginx mesh-proxy configuration dynamically from docker-compose'
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
        help='Base domain (e.g., mesh-app.local)'
    )
    parser.add_argument(
        '--base-cert',
        type=str,
        default='mesh-app.crt',
        help='Base SSL certificate filename'
    )
    parser.add_argument(
        '--base-key',
        type=str,
        default='mesh-app.key',
        help='Base SSL key filename'
    )
    parser.add_argument(
        '--output',
        type=Path,
        required=True,
        help='Output path for generated nginx configuration'
    )
    parser.add_argument(
        '--service-mtls',
        action='append',
        default=[],
        help='Service names that require mTLS (can be specified multiple times)'
    )

    args = parser.parse_args()

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

    if not segments_dir.exists():
        print(f"Error: Segments directory not found: {segments_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse docker-compose file
    print(f"Parsing docker-compose file: {args.compose}")
    compose_data = parse_docker_compose(args.compose, script_dir)
    services = compose_data.get('services', [])

    # Filter out the proxy service itself (we don't proxy to ourselves)
    services = [svc for svc in services if svc['name'] not in ['proxy', 'mesh-proxy']]

    # Apply mTLS flag to specified services
    for service in services:
        if service['name'] in args.service_mtls:
            service['mtls'] = True

    print(f"Found {len(services)} services:")
    for svc in services:
        mtls_flag = " (mTLS)" if svc.get('mtls') else ""
        print(f"  - {svc['name']}: {svc['subdomain']}.{args.domain}:{svc['port']}{mtls_flag}")

    # Build nginx configuration
    print(f"\nBuilding nginx configuration...")
    nginx_config = build_nginx_config(
        services=services,
        base_domain=args.domain,
        base_cert=args.base_cert,
        base_key=args.base_key,
        template_dir=template_dir,
        segments_dir=segments_dir
    )

    # Write output file
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(nginx_config)
    print(f"✓ Configuration written to: {args.output}")


if __name__ == '__main__':
    main()
