#!/usr/bin/env python3
"""Publish generalized runtime-image release metadata for standard update/reconcile."""
import json,os,sys
from pathlib import Path
from catalog_artifacts import sha
from catalog_publish import REPO,publish_asset
from image_identity import verify_archive
from operations import atomic
from runtime_reconcile import targets
from repository_lock import publication_lock
import storage_placement

def main(path):
    if os.geteuid()!=0 or Path('/etc/isle-mesh/agent/agent.mode').read_text().strip()!='core':raise PermissionError('Publish runtime artifacts on the core with sudo isle update publish-runtime <manifest>')
    with publication_lock(REPO):
        publish_runtime(path)
        storage_placement.after_publication(REPO,dataset=storage_placement.DATASET_APT)

def kept(item,destination):
    """An image the release does not change: the published file has to be here, whole, and be that image."""
    image=item['image'];archive=image['archive'];target=destination/(archive['sha256']+'.tar')
    if not target.is_file() or target.stat().st_size!=archive['size'] or sha(target)!=archive['sha256']:
        raise ValueError('The manifest keeps the image of '+item['container']+' as this core publishes it ('+target.name+'), and this core does not publish that file. Nothing was published; the packet has to carry that image')
    verify_archive(target,image)

def publish_runtime(path):
    path=Path(path).resolve();m=json.loads(path.read_text());destination=REPO/'runtime';destination.mkdir(parents=True,exist_ok=True)
    rows=[item for mode in ('core','remote') for item in targets(m,mode,source=True)]
    # First the images the release does not change: each names the file this core publishes already.  One that is
    # not here stops the publication before a single file is added.
    for item in rows:
        if 'source' not in item['image']['archive']:kept(item,destination)
    for item in rows:
        image=item['image']
        if 'source' not in image['archive']:continue
        source=Path(image['archive']['source']);source=source if source.is_absolute() else path.parent/source
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
