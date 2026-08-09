# shells/tools — synced from polari-app-shell

SOURCE OF TRUTH: the `polari-app-shell` repo (pol-core,
`polari-suite/polari-app-shell/shells/`). This directory is a synced
copy so the isle-mesh-cli deb is self-contained: build-cli-deb.sh
stages these builders + icons at `/usr/share/isle-mesh/shells/` on
every device (branded launchers, §4 fix 2).

When the builders change in polari-app-shell, re-sync:

    scp pol-core:~/Desktop/polari-suite/polari-app-shell/shells/build-*.sh tools/
    scp pol-core:~/Desktop/polari-suite/polari-app-shell/shells/icons/* tools/icons/

then rebuild the CLI deb (build-cli-deb.sh, bump --version).
