#!/usr/bin/env python3
"""
generate-compose.py

Generates docker-compose.yml for isle-agent with conditional bridge support.
Uses Jinja2 templating to include bridges only when they exist on the system.

Usage:
    python3 generate-compose.py \
        --output /etc/isle-mesh/agent/docker-compose.mdns.yml \
        --mode mdns \
        --bridges isle-br-0

    python3 generate-compose.py \
        --output /etc/isle-mesh/agent/docker-compose.yml \
        --mode lightweight

    python3 generate-compose.py \
        --output /etc/isle-mesh/agent/docker-compose.yml \
        --mode standalone
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape


VALID_MODES = ['standalone', 'mdns', 'lightweight']


def detect_bridges() -> list:
    """
    Detect available isle-br-X bridges on the system.
    Returns list of bridge names.
    """
    import subprocess

    try:
        result = subprocess.run(
            ['ip', 'link', 'show'],
            capture_output=True,
            text=True,
            check=True
        )

        bridges = []
        for line in result.stdout.split('\n'):
            if 'isle-br-' in line:
                # Extract bridge name from line like: "4: isle-br-0: <BROADCAST..."
                parts = line.split(':')
                if len(parts) >= 2:
                    bridge_name = parts[1].strip()
                    if bridge_name.startswith('isle-br-'):
                        bridges.append(bridge_name)

        return sorted(list(set(bridges)))

    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not detect bridges: {e}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("Warning: 'ip' command not found", file=sys.stderr)
        return []


def generate_compose(
    mode: str,
    bridges: list,
    output_path: Path,
    template_dir: Path,
    enable_mdns: bool = None
) -> str:
    """
    Generate docker-compose file using Jinja2 template.

    Args:
        mode: 'standalone', 'mdns', or 'lightweight'
        bridges: List of bridge names to include (e.g., ['isle-br-0'])
        output_path: Where to write the generated file
        template_dir: Directory containing templates
        enable_mdns: Override mDNS setting (defaults based on mode)

    Returns:
        Path to generated file
    """
    # Determine mDNS setting
    if enable_mdns is None:
        enable_mdns = (mode == 'mdns')

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True
    )

    # Prepare template context
    context = {
        'mode': mode,
        'enable_mdns': enable_mdns,
        'bridges': bridges,
        'timestamp': datetime.now().isoformat()
    }

    # Load and render template
    template = env.get_template('docker-compose.yml.j2')
    content = template.render(context)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)

    return str(output_path)


def main():
    parser = argparse.ArgumentParser(
        description='Generate docker-compose.yml for isle-agent with conditional bridges'
    )

    parser.add_argument(
        '--mode',
        type=str,
        choices=VALID_MODES,
        default='standalone',
        help='Agent mode: standalone (no bridges), mdns (with mDNS), or lightweight (without mDNS)'
    )

    parser.add_argument(
        '--bridges',
        type=str,
        nargs='*',
        help='Bridge names to include (e.g., isle-br-0 isle-br-1). Use --auto-detect to detect automatically.'
    )

    parser.add_argument(
        '--auto-detect',
        action='store_true',
        help='Auto-detect available isle-br-X bridges'
    )

    parser.add_argument(
        '--output',
        type=Path,
        required=True,
        help='Output path for generated docker-compose file'
    )

    parser.add_argument(
        '--enable-mdns',
        action='store_true',
        default=None,
        help='Force enable mDNS (overrides mode default)'
    )

    parser.add_argument(
        '--disable-mdns',
        action='store_true',
        help='Force disable mDNS (overrides mode default)'
    )

    args = parser.parse_args()

    # Determine script and template directories
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    template_dir = project_root / 'templates'

    # Verify template directory exists
    if not template_dir.exists():
        print(f"Error: Template directory not found: {template_dir}", file=sys.stderr)
        sys.exit(1)

    # Determine bridges
    bridges = []
    if args.auto_detect:
        print("Auto-detecting bridges...")
        bridges = detect_bridges()
        if bridges:
            print(f"Found bridges: {', '.join(bridges)}")
        else:
            print("No isle-br-X bridges found")
    elif args.bridges:
        bridges = args.bridges
        print(f"Using specified bridges: {', '.join(bridges)}")
    else:
        print("No bridges specified (standalone mode)")

    # Determine mDNS setting
    enable_mdns = None
    if args.enable_mdns:
        enable_mdns = True
    elif args.disable_mdns:
        enable_mdns = False

    # Generate compose file
    print(f"\nGenerating docker-compose file:")
    print(f"  Mode: {args.mode}")
    print(f"  mDNS: {enable_mdns if enable_mdns is not None else 'auto (based on mode)'}")
    print(f"  Bridges: {len(bridges)}")
    for bridge in bridges:
        print(f"    - {bridge}")
    print(f"  Output: {args.output}")
    print()

    try:
        output_file = generate_compose(
            mode=args.mode,
            bridges=bridges,
            output_path=args.output,
            template_dir=template_dir,
            enable_mdns=enable_mdns
        )

        print(f"✓ Docker compose file generated: {output_file}")

        # Show usage instructions
        print("\nTo use this compose file:")
        print(f"  docker compose -f {output_file} up -d")

    except Exception as e:
        print(f"Error generating compose file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
