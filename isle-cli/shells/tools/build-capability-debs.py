#!/usr/bin/env python3
"""Build small dependency-only packages. No maintainer scripts or app deployment."""
import argparse, pathlib, subprocess, tempfile
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--version',default='0.1.0');args=p.parse_args()
out=pathlib.Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
profiles={
 'mesh':('docker-ce | docker.io, docker-compose-plugin | docker-compose-v2','Docker Engine and Compose for external Mesh Apps'),
 'polari':('isle-capability-mesh (= '+args.version+')','Container baseline for Polari nodes; images and modules are separate'),
 'kvm':('qemu-system-x86, libvirt-daemon-system, libvirt-clients','QEMU and libvirt for amd64 KVM hardware apps'),
 'shell':('polari-shell-core','Shared Polari shell runtime for app launchers'),
}
for name,(depends,description) in profiles.items():
 package='isle-capability-'+name
 with tempfile.TemporaryDirectory() as tmp:
  root=pathlib.Path(tmp);(root/'DEBIAN').mkdir()
  (root/'DEBIAN/control').write_text(f'Package: {package}\nVersion: {args.version}\nArchitecture: amd64\nMaintainer: Polari Project\nSection: metapackages\nPriority: optional\nDepends: {depends}\nDescription: {description}\n Dependency package only. Existing compatible Debian providers satisfy it.\n After installation, use isle capabilities enable to verify this device.\n Membership, certificate trust, resources and app-specific hardware remain separate.\n')
  subprocess.run(['dpkg-deb','--root-owner-group','--build',str(root),str(out/f'{package}_{args.version}_amd64.deb')],check=True)
