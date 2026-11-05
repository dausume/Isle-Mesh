#!/usr/bin/env python3
"""Shared health-path contract for release admission, deployment and status."""
def validate_path(path):
    if (not isinstance(path,str) or not path.startswith('/') or len(path)>512
            or '#' in path or any(ord(c)<33 for c in path)):
        raise ValueError('Invalid health path')
    return path

if __name__=='__main__':
    import sys
    try:
        if len(sys.argv)!=2:raise ValueError('Expected one health path')
        validate_path(sys.argv[1])
    except ValueError as error:
        print(str(error),file=sys.stderr);sys.exit(1)
