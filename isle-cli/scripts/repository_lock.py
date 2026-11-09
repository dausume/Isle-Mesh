"""One publication lock for APT, runtime images, catalog assets and snapshots."""
from contextlib import contextmanager
import fcntl
import os


@contextmanager
def publication_lock(repo):
    # Same lock as apt-repo.sh. Keep it outside the public content tree.
    repo.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(repo.parent / '.publish.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
