#!/usr/bin/env python3
"""The largest request body an app takes through its device's agent: one rule for the release, the deploy and the agent.

A size is `0` (no limit) or a whole number with an optional k, m or g, as nginx reads it: 64m, 2g.  An app that
declares none keeps the agent's default, which is nginx's own: 1 MiB.  A release declares it as deploy.maxUpload,
a deploy by hand as `isle app deploy --max-upload <size>`; the agent keeps it on the app's entry in its registry
(`maxBody`) and its generator writes it for that app only.  The same pattern is in agent-manager.sh and in
generate-nginx-configs.sh, which cannot import this file.
"""
import re

SIZE = re.compile(r'(?:0|[1-9][0-9]{0,5}[kmg]?)\Z')
WORDS = 'an upload size is 0 (no limit) or a whole number with k, m or g: 64m, 2g'


def validate(value):
    if not isinstance(value, str) or not SIZE.match(value):
        raise ValueError('Invalid upload size (%s)' % WORDS)
    return value


if __name__ == '__main__':
    import sys
    try:
        if len(sys.argv) != 2: raise ValueError('Expected one upload size')
        validate(sys.argv[1])
    except ValueError as error:
        print(str(error), file=sys.stderr); sys.exit(1)
