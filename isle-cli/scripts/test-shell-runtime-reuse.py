#!/usr/bin/env python3
"""A launcher must reuse installed merged runtime, never replace its package."""
import json,os
from pathlib import Path
import subprocess,tempfile,unittest
SOURCE=Path(__file__).with_name('shell.sh')
class RuntimeReuse(unittest.TestCase):
    def test_installed_owner_reuses_files_or_refuses_incomplete_runtime(self):
        for owner in ('polari-shell-core','polari-complete','polari-complete-offline'):
            for complete in (True,False):
                with self.subTest(owner=owner,complete=complete),tempfile.TemporaryDirectory() as name:
                    root=Path(name);binary=root/'bin';binary.mkdir();runtime=root/'runtime'
                    if complete:
                        (runtime/'bin').mkdir(parents=True);(runtime/'lib/app').mkdir(parents=True)
                        executable=runtime/'bin/polari-shell';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o755)
                    script=root/'shell.sh';script.write_text(SOURCE.read_text().replace('/opt/polari-shell',str(runtime)))
                    query=binary/'dpkg-query';query.write_text('#!/bin/sh\n[ "$3" = "$OWNER" ] || exit 1\nprintf installed\n');query.chmod(0o755)
                    sudo=binary/'sudo';sudo.write_text('#!/bin/sh\necho forbidden > "$CALLS"\nexit 91\n');sudo.chmod(0o755)
                    environment=dict(os.environ,PATH=str(binary)+':'+os.environ['PATH'],OWNER=owner,CALLS=str(root/'calls'))
                    result=subprocess.run(['bash',str(script),'ensure-core'],env=environment,capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0 if complete else 1,result.stdout+result.stderr)
                    self.assertFalse((root/'calls').exists(),'attempted second package install')
                    self.assertIn('already installed' if complete else 'repair/reinstall',result.stdout)
if __name__=='__main__':unittest.main()
