#!/usr/bin/env bash
# Removes CCAS. Never deletes anything — everything is moved to .claude_trash/
# in this checkout. --purge also moves the account data there. ~/.claude is
# never touched.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${CCAS_BIN_DIR:-$HOME/.local/bin}"
SHARE="${CCAS_SHARE_DIR:-$HOME/.local/share/ccas}"
TRASH="${CCAS_TRASH:-$SRC/.claude_trash}"
ACCOUNTS="${CCAS_ACCOUNTS_ROOT:-$HOME/.cc-accounts}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$TRASH"

CCAS_SRC_DIR="$SRC" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["CCAS_SRC_DIR"])
sys.path.insert(0, os.environ.get("CCAS_SHARE_DIR", os.path.expanduser("~/.local/share/ccas")))
from ccas import paths, waybar
for path, comment in ((paths.waybar_config(), "//"), (paths.bashrc(), "#")):
    if path.exists():
        waybar.strip(path, comment)
PY

if [ -e "$BIN_DIR/ccs" ]; then
  mv "$BIN_DIR/ccs" "$TRASH/ccs-$STAMP"
fi
if [ -d "$SHARE" ]; then
  mv "$SHARE" "$TRASH/ccas-share-$STAMP"
fi

if [ "${1:-}" = "--purge" ] && [ -d "$ACCOUNTS" ]; then
  mv "$ACCOUNTS" "$TRASH/cc-accounts-$STAMP"
  echo "Account data moved to $TRASH (not deleted)."
fi

SYSTEMD_DIR="${CCAS_SYSTEMD_DIR:-$HOME/.config/systemd/user}"
if [ -z "${CCAS_SKIP_SYSTEMD:-}" ] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now ccas-poll.timer 2>/dev/null || true
fi
for unit in ccas-poll.service ccas-poll.timer; do
  if [ -e "$SYSTEMD_DIR/$unit" ]; then
    mv "$SYSTEMD_DIR/$unit" "$TRASH/$unit-$STAMP"
  fi
done

if [ -z "${CCAS_NO_RELOAD:-}" ]; then
  killall -SIGUSR2 waybar 2>/dev/null || true
fi
echo "ccas uninstalled."
