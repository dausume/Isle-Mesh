#!/usr/bin/env python3
"""Admit release-defined Polari providers using the same typed artifact verifier."""
import json, subprocess, os
from pathlib import Path
from catalog_artifacts import CACHE, fetch
from image_identity import verify_archive, admit_installed
from runtime_reconcile import targets

DESIRED=Path('/var/lib/isle-mesh/desired-release.json')
def definitions(release, role):
    # Existing signed release schema identifies these provider targets explicitly.
    rows=targets(release,'core')
    names={'backend':'prf-isle-backend','frontend':'prf-isle-frontend'}
    result={}
    for component in ('backend','frontend'):
        if role not in ('both',component):continue
        found=[r for r in rows if r['container']==names[component]]
        if len(found)!=1:raise ValueError('Release lacks an unambiguous '+component+' provider image; update to a provider-capable release')
        result[component]=found[0]['image']
    return result

def resolve(role, release=None):
    from update import validate
    if release is None:
        if not DESIRED.exists():raise ValueError('No admitted provider release; run isle update plan then sudo isle update apply <release-id>')
        operation=os.environ.get('ISLE_OPERATION_ID','')
        from operations import path_for, atomic
        snapshot=path_for(operation)/'provider-release.json' if operation else None
        if snapshot is not None and snapshot.exists():release=json.loads(snapshot.read_text())
        else:
            release=json.loads(DESIRED.read_text())
            validate(release)
            if snapshot is not None:atomic(snapshot,release,0o600)
    validate(release)
    result={}
    for component,image in definitions(release,role).items():
        archive=fetch(image['archive'],CACHE/'runtime'/(image['archive']['sha256']+'.tar'))
        verified=verify_archive(archive,image)
        current=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image['configDigest']],capture_output=True,text=True)
        if current.returncode or current.stdout.strip() not in verified:
            subprocess.run(['docker','load','--input',str(archive)],check=True)
        # Docker may address an OCI image by manifest rather than config digest.
        for candidate in [image['configDigest'],image.get('manifestDigest'),image['ref']]:
            if not candidate:continue
            current=subprocess.run(['docker','image','inspect','--format','{{.Id}}',candidate],capture_output=True,text=True)
            if current.returncode==0 and current.stdout.strip() in verified:
                result[component]=admit_installed(current.stdout.strip(),verified) or current.stdout.strip()
                break
        else:raise ValueError('Published provider did not load with its admitted identity: '+component)
    return result
