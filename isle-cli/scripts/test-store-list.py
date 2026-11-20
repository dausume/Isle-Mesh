#!/usr/bin/env python3
"""`isle store list` prints every catalog entry, also one published without the optional fields.

Found on pol with CLI release 88: one entry without `provides_engine` ended the list with a Python error.
The real store.sh runs here; only curl is a stand-in that answers with a catalog."""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = {'ok': True, 'count': 3, 'entries': [
    {'name': 'full', 'kind': 'mesh-app', 'title': 'Full entry', 'description': 'All fields.', 'provides_engine': 'cad',
     'instances': [{'device': 'core-a'}, {'device': None}]},
    {'name': 'bare', 'kind': 'mesh-app', 'title': 'Bare entry', 'description': 'No optional field.'},
    {'name': 'least'},
]}


def main():
    with tempfile.TemporaryDirectory() as work:
        curl = pathlib.Path(work, 'curl')
        curl.write_text('#!/bin/sh\ncat "%s/catalog.json"\n' % work); curl.chmod(0o755)
        pathlib.Path(work, 'catalog.json').write_text(json.dumps(CATALOG))
        env = dict(os.environ, PATH=work + os.pathsep + os.environ['PATH'])
        run = subprocess.run(['bash', str(HERE / 'store.sh'), 'list'], env=env, capture_output=True, text=True, timeout=60)
    out = run.stdout
    checks = [
        ('the command succeeds', run.returncode == 0),
        ('no Python error', 'Traceback' not in run.stderr and 'Error' not in run.stderr),
        ('the count line', '3 apps' in out),
        ('the full entry with its engine', 'full' in out and '[engine: cad]' in out),
        ('instances with and without a device', 'runs x2 on ?, core-a' in out),
        ('the entry without optional fields', 'Bare entry' in out and out.count('[engine:') == 1),
        ('the entry with a name only', '(?)' in out and 'least' in out),
        ('no stand-in for a failed catalog', 'no catalog' not in out),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks: print('%s  %s' % ('PASS' if ok else 'FAIL', name))
    if failed: print(out, run.stderr, sep='\n'); sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
