"""Local capability prerequisites. Existing providers are retained, not replaced."""
import json, platform, shutil, subprocess, sys
PROFILES = {
 'mesh-app': {'title':'Mesh hosting', 'package':'isle-capability-mesh', 'dependencies':'Docker Engine and Docker Compose', 'required':['runtime.docker.usable','runtime.compose.usable','agent.running']},
 'polari-instance': {'title':'Polari node hosting', 'package':'isle-capability-polari', 'dependencies':'Docker Engine and Docker Compose; app modules and images are separate', 'required':['runtime.docker.usable','runtime.compose.usable','agent.running']},
 'hardware-vm': {'title':'KVM hardware hosting', 'package':'isle-capability-kvm', 'dependencies':'QEMU, libvirt and usable KVM; guest images and device passthrough are separate', 'required':['runtime.libvirt.usable','runtime.kvm.usable']},
 'shell-app': {'title':'Shell launchers', 'package':'isle-capability-shell', 'dependencies':'Polari shared shell runtime; Isle access and certificate trust', 'required':['runtime.shell.installed','access.polari-frontend.https']},
}
def plan(profile, which=shutil.which, run=subprocess.run, arch=None):
 if profile not in PROFILES: raise ValueError('Choose '+', '.join(PROFILES))
 result=dict(PROFILES[profile], profile=profile, packages=[], service='', supported=True)
 if profile in ('mesh-app','polari-instance'):
  result['service']='docker'
  if not which('docker'): result['packages'] += ['docker.io','docker-compose-v2']
  else:
   compose=run(['docker','compose','version'],capture_output=True,timeout=15)
   if compose.returncode:
    # Preserve Docker CE when it is already the installed provider.
    ce=run(['dpkg-query','-W','-f=${Status}','docker-ce'],capture_output=True,text=True,timeout=15)
    result['packages'].append('docker-compose-plugin' if ce.returncode==0 and 'install ok installed' in ce.stdout else 'docker-compose-v2')
 elif profile=='hardware-vm':
  arch=arch or platform.machine()
  qemu={'x86_64':('qemu-system-x86_64','qemu-system-x86'),'aarch64':('qemu-system-aarch64','qemu-system-arm')}.get(arch)
  if not qemu: result.update(supported=False, reason='No tested QEMU package mapping for '+arch);return result
  if not which(qemu[0]): result['packages'].append(qemu[1])
  if not which('virsh'): result['packages'].append('libvirt-clients')
  # Service binaries may live outside a caller's PATH.
  if not which('libvirtd') and not which('virtqemud'): result['packages'].append('libvirt-daemon-system')
  result['service']='virtqemud' if which('virtqemud') and not which('libvirtd') else 'libvirtd'
 elif not which('polari-app-shell'): result['packages'].append('polari-shell-core')
 result['command']='sudo isle capabilities enable '+profile
 return result
if __name__=='__main__':
 try:
  if len(sys.argv)==1: print(json.dumps(PROFILES,indent=2))
  elif len(sys.argv)==2: print(json.dumps(plan(sys.argv[1]),indent=2))
  else: raise ValueError('Expected one local profile; no remote target is accepted')
 except (ValueError,subprocess.SubprocessError) as e: sys.exit(str(e))
