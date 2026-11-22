#!/usr/bin/env python3
"""What the start of an app does with its storage slots.

An app whose release declares storage slots is started only when every slot is bound and its partition is there.
`isle app deploy` checks that and writes each slot's folder into the app's overlay (app_overlay.py).  This file
holds the two pieces around it: what an install does with the list of a release, and the check of a start that
does not write the overlay again.

Nothing here starts, stops or changes an app.
"""
import os
from pathlib import Path

import yaml

import storage_roles as sr
import storage_slots

OVERLAY = 'isle-overlay.yml'
ERRORS = storage_slots.ERRORS + (yaml.YAMLError,)


def at_install(io, app, listed):
    """An install keeps what the release declares, then says whether the app can be started with it.
    listed: the list `storageSlots` of the release manifest, or None when the release has none.  An app that
    declares no slot and never did is left as it is: nothing is written for it.
    ValueError before anything is deployed: a slot that is bound here would be dropped or would change its role,
    or a slot cannot be given as it stands.  The declaration is kept in the second case, so the slot can be bound."""
    if not listed and not os.path.lexists(storage_slots._file(io, app)): return []
    storage_slots.write_declaration(io, app, listed or [])
    try: return storage_slots.binds(io, app)
    except ValueError as exc:
        raise ValueError('%s. What the release declares is kept, and nothing was deployed' % exc)


def given(overlay):
    """The slot folders an overlay gives: {(folder, where the app sees it, read-only)}."""
    out = set()
    for service in ((overlay or {}).get('services') or {}).values():
        for volume in (service or {}).get('volumes') or []:
            if isinstance(volume, dict) and volume.get('type') == 'bind' and '/%s/' % storage_slots.FOLDER in str(volume.get('source')):
                out.add((volume['source'], volume.get('target'), bool(volume.get('read_only'))))
    return out


def gate(directory, io=None):
    """None when the app kept in this folder may be started with the overlay it has, else the words that refuse it.
    For a start that does not write the overlay again (`isle reconcile`): every slot must still be usable, and the
    overlay must give exactly the folders that are bound now."""
    directory, io = Path(directory), io or sr.Io()
    if directory.parent != Path(io.apps_dir) or not os.path.lexists(directory / storage_slots.FILE): return None
    app = directory.name
    try:
        bound = {(b['source'], b['target'], b['readOnly']) for b in storage_slots.binds(io, app)}
        deployed = given(yaml.safe_load((directory / OVERLAY).read_text()))
    except ERRORS as exc:
        return str(exc) or exc.__class__.__name__
    if bound != deployed:
        return ('the storage slots of %s are not the ones it was deployed with. Install or deploy it again, so that it is '
                'given the folders that are bound now (see: isle storage slot list --table)' % app)
    return None
