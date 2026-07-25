#!/usr/bin/env bash
# CCAS installer. Idempotent — safe to re-run after a system update.
#
# Never deletes anything: a previous staged copy is moved to the trash
# directory, not removed. Never writes to ~/.claude.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${CCAS_BIN_DIR:-$HOME/.local/bin}"
SHARE="${CCAS_SHARE_DIR:-$HOME/.local/share/ccas}"
TRASH="${CCAS_TRASH:-$HOME/.claude_trash}"
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
