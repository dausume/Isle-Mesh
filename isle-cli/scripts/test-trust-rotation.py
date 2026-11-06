#!/usr/bin/env python3
"""Real detached-signature admission; fake transport, no host trust changes."""
import os,subprocess,tempfile,unittest,time,shutil
from pathlib import Path
class Rotation(unittest.TestCase):
 def test_signed_public_root_and_negative_controls(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);home=root/'signer';home.mkdir(mode=0o700)
   def run(argv):return subprocess.run(argv,check=True,capture_output=True,text=True)
   gpg=['gpg','--homedir',str(home),'--batch','--pinentry-mode','loopback','--passphrase','']
   run(gpg+['--quick-gen-key','Isolated rotation test','ed25519','sign','0'])
   key=root/'trusted.gpg'
   with key.open('wb') as out:subprocess.run(gpg+['--export'],stdout=out,stderr=subprocess.DEVNULL,check=True)
   cert=root/'public.crt'
   run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'private.key'),'-out',str(cert),'-days','1','-subj','/CN=Isolated Test Root','-addext','basicConstraints=critical,CA:TRUE'])
   sig=root/'public.asc'
   run(gpg+['--armor','--detach-sign','--output',str(sig),str(cert)])
   script=Path(__file__).with_name('trust.sh').read_text().split('case "${1:-status}" in')[0]
   script=script.replace('/usr/share/keyrings/isle-archive-keyring.gpg',str(key))
   helper=root/'helper.sh';helper.write_text(script)
   driver='''source "$HELPER"
CA="$CURRENT_CERT"
curl() {
 local output="" previous="" value
 for value in "$@"; do [ "$previous" != -o ] || output="$value"; previous="$value"; done
 if [[ "${*: -1}" == *.asc ]]; then cp "$SIGNATURE" "$output"; else cp "$CERTIFICATE" "$output"; fi
}
signed_rotation "$RESULT"
'''
   env=dict(os.environ,HELPER=str(helper),SIGNATURE=str(sig),CERTIFICATE=str(cert),RESULT=str(root/'result'),CURRENT_CERT=str(root/'current.crt'))
   def probe():return subprocess.run(['bash','-c',driver],env=env,capture_output=True,text=True)
   self.assertEqual(probe().returncode,0)
   current=root/'current.crt'
   shutil.copyfile(cert,current)
   self.assertEqual(probe().returncode,0)
   # A correctly signed but older root cannot roll back the installed root.
   time.sleep(1.1)
   run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'current.key'),'-out',str(current),'-days','1','-subj','/CN=New Current Root','-addext','basicConstraints=critical,CA:TRUE'])
   self.assertNotEqual(probe().returncode,0)
   current.unlink()
   # An unauthenticated certificate replacement is rejected even if valid PEM.
   run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'other.key'),'-out',str(cert),'-days','1','-subj','/CN=Untrusted Replacement','-addext','basicConstraints=critical,CA:TRUE'])
   self.assertNotEqual(probe().returncode,0)
   # Trusted signing cannot make a non-certificate into a root.
   cert.write_text('not a certificate')
   run(gpg+['--yes','--armor','--detach-sign','--output',str(sig),str(cert)])
   self.assertNotEqual(probe().returncode,0)
   key.unlink();self.assertNotEqual(probe().returncode,0)
if __name__=='__main__':unittest.main()
