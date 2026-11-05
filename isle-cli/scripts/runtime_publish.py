#!/usr/bin/env python3
"""Publish generalized runtime-image release metadata for standard update/reconcile."""
import json,os,sys
from pathlib import Path
from catalog_publish import REPO,publish_asset
from image_identity import verify_archive
from operations import atomic
from runtime_reconcile import targets

def main(path):
    if os.geteuid()!=0 or Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()!='core':raise PermissionError('Publish runtime artifacts on the core with sudo isle update publish-runtime <manifest>')
    path=Path(path).resolve();m=json.loads(path.read_text());destination=REPO/'runtime';destination.mkdir(parents=True,exist_ok=True)
    for mode in ('core','remote'):
        for item in targets(m,mode,source=True):
            image=item['image'];source=Path(image['archive']['source']);source=source if source.is_absolute() else path.parent/source
            verify_archive(source,image)
            publish_asset(image['archive'],path.parent,destination,REPO)
    for mode in ('core','remote'):targets(m,mode)
    atomic(REPO/'runtime-release.json',m)
    print('Runtime artifacts published. isle apt-repo publish includes them in the exact versioned update manifest.')
if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('Usage: isle update publish-runtime <manifest>')
        main(sys.argv[1])
    except (OSError,ValueError,KeyError) as e:print(str(e),file=sys.stderr);sys.exit(1)
