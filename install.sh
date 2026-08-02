#!/usr/bin/env bash
# CCAS installer. Idempotent — safe to re-run after a system update.
#
# Never deletes anything: a previous staged copy is moved to the trash
# directory, not removed. Never writes to ~/.claude.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${CCAS_BIN_DIR:-$HOME/.local/bin}"
SHARE="${CCAS_SHARE_DIR:-$HOME/.local/share/ccas}"
TRASH="${CCAS_TRASH:-$SRC/.claude_trash}"
ACCOUNTS="${CCAS_ACCOUNTS_ROOT:-$HOME/.cc-accounts}"

mkdir -p "$BIN_DIR" "$SHARE" "$TRASH" "$ACCOUNTS"

# Stage the package so the repo can move without breaking the install.
#
# Only when it actually differs: this script is re-run after every code change,
# and a staged copy identical to the one replacing it is worth nothing — kept,
# it made 99 trash entries in a day. Skipping the move is not a deletion, which
# is why the check is here and not an `rm` below.
#
# __pycache__ is excluded from the copy rather than trashed after it, so it does
# not count as a difference on the next run.
#
# Staged aside and swapped in, never extracted over the live package. A running
# Claude Code session spawns `ccs` through its statusline every few hundred
# milliseconds, and extracting in place leaves it importable but incomplete —
# `ccas/__init__.py` there while `ccas/cli.py` is not yet. Measured 2026-07-28:
# 1.2 ms per install with the package unusable, and 0.2 ms with the launcher at
# zero bytes below. Two renames leave a window of one rename instead of one tar.
if [ ! -d "$SHARE/ccas" ] || ! diff -r -q -x __pycache__ -x '*.pyc' \
     "$SRC/ccas" "$SHARE/ccas" >/dev/null 2>&1; then
  # mktemp rather than a fixed name, so the staging directory is always empty
  # and nothing has to be deleted to make it so — the never-delete rule reaches
  # here too. rmdir at the end removes it only if it is empty, which it is.
  STAGE="$(mktemp -d "$SHARE/.staging-XXXXXX")"
  tar -C "$SRC" --exclude=__pycache__ --exclude='*.pyc' -cf - ccas | tar -C "$STAGE" -xf -
  if [ -d "$SHARE/ccas" ]; then
    mv "$SHARE/ccas" "$TRASH/ccas-pkg-$(date +%Y%m%d-%H%M%S)-$$"
  fi
  mv "$STAGE/ccas" "$SHARE/ccas"
  rmdir "$STAGE" 2>/dev/null || true
fi

# Written beside the launcher and renamed over it: rename(2) is atomic, so a
# spawn either gets the whole old file or the whole new one. `cat >` truncates
# the file a live session is about to exec.
#
# The trash location is stamped in because the staged package cannot derive it:
# it runs out of $SHARE and has no way back to the checkout it was copied from,
# and the never-delete rule puts the trash in that checkout. setdefault, so an
# explicit CCAS_TRASH in the environment still wins — the tests rely on that.
cat > "$BIN_DIR/.ccs.$$" <<EOF
#!/usr/bin/env python3
import os
import sys
os.environ.setdefault("CCAS_TRASH", "$TRASH")
sys.path.insert(0, "$SHARE")
from ccas.cli import main
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
EOF
chmod +x "$BIN_DIR/.ccs.$$"
mv "$BIN_DIR/.ccs.$$" "$BIN_DIR/ccs"

export CCAS_CCS_BIN="${CCAS_CCS_BIN:-$BIN_DIR/ccs}"

# The panel's stylesheet, once. Never overwritten: it is the user's the moment
# it exists, the same stance CCAS takes toward style.css. A reinstall that
# reverted their colours would be a deletion in all but name.
MENU_CSS="${CCAS_MENU_CSS:-$HOME/.config/ccas/menu.css}"
if [ ! -e "$MENU_CSS" ]; then
  mkdir -p "$(dirname "$MENU_CSS")"
  cp "$SRC/assets/menu.css" "$MENU_CSS"
fi

# The poll timer. Unlike menu.css these are CCAS's files and are rewritten on
# every install — but never overwritten in place: a user who retimed the poll
# gets their version back out of the trash.
SYSTEMD_DIR="${CCAS_SYSTEMD_DIR:-$HOME/.config/systemd/user}"
mkdir -p "$SYSTEMD_DIR"
for unit in ccas-poll.service ccas-poll.timer; do
  rendered="$(sed "s|@CCS@|$BIN_DIR/ccs|g" "$SRC/assets/$unit")"
  if [ -e "$SYSTEMD_DIR/$unit" ]; then
    # Same reasoning as the package above: an unchanged unit is not rewritten,
    # so re-running the installer costs nothing. It is compared in memory rather
    # than through a temp file, so there is never a scratch copy to delete.
    [ "$rendered" = "$(cat "$SYSTEMD_DIR/$unit")" ] && continue
    mv "$SYSTEMD_DIR/$unit" "$TRASH/$unit-$(date +%Y%m%d-%H%M%S)-$$"
  fi
  printf '%s\n' "$rendered" > "$SYSTEMD_DIR/$unit"
done
if [ -z "${CCAS_SKIP_SYSTEMD:-}" ] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
  systemctl --user enable --now ccas-poll.timer || true
fi

# Re-link every existing account so entries added by a Claude Code update get
# shared rather than stranded inside one account directory.
"$BIN_DIR/ccs" relink || true

if [ -n "${CCAS_SKIP_RELOAD:-}" ]; then
  CCAS_NO_RELOAD=1 "$BIN_DIR/ccs" config
else
  "$BIN_DIR/ccs" config
fi

echo "ccas installed to $BIN_DIR/ccs"
echo "Run 'ccs add' to add your first account, or click the dim ✻ in Waybar."
echo "Usage poll: systemctl --user status ccas-poll.timer"
