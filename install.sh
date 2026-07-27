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
if [ -d "$SHARE/ccas" ]; then
  mv "$SHARE/ccas" "$TRASH/ccas-pkg-$(date +%Y%m%d-%H%M%S)-$$"
fi
cp -r "$SRC/ccas" "$SHARE/ccas"
find "$SHARE/ccas" -name __pycache__ -type d -exec mv {} "$TRASH/" \; 2>/dev/null || true

cat > "$BIN_DIR/ccs" <<EOF
#!/usr/bin/env python3
import sys
sys.path.insert(0, "$SHARE")
from ccas.cli import main
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
EOF
chmod +x "$BIN_DIR/ccs"

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
  if [ -e "$SYSTEMD_DIR/$unit" ]; then
    mv "$SYSTEMD_DIR/$unit" "$TRASH/$unit-$(date +%Y%m%d-%H%M%S)-$$"
  fi
  sed "s|@CCS@|$BIN_DIR/ccs|g" "$SRC/assets/$unit" > "$SYSTEMD_DIR/$unit"
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
