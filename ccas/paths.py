"""Every filesystem location CCAS touches, overridable for tests."""
import os
from pathlib import Path

GLYPH = "✻"

PALETTE = [
    ("peach", "#fab387"),
    ("red", "#f38ba8"),
    ("mauve", "#cba6f7"),
    ("blue", "#89b4fa"),
    ("teal", "#94e2d5"),
    ("green", "#a6e3a1"),
    ("yellow", "#f9e2af"),
    ("pink", "#f5c2e7"),
]

# "custom" is appended, never inserted: `ccs display <slug> <mode>` and the
# panel dropdown both address modes by position in this list, so inserting would
# silently repoint every existing selection.
DISPLAY_MODES = ["nickname", "index", "claude code", "icon only", "custom"]

# How many recent sessions the resume picker lists. Both doors scroll, so this is
# a bound on the scan, not on what fits on screen.
HIST_SLOTS = 300

# The recorded usage reading, in the account directory. Per-account by
# definition — a link to a shared one would report the wrong account's quota.
USAGE_FILE = "usage.json"

# Never symlinked into an account: the first two are the account's identity, and
# the rest are per-account files CCAS itself used to generate. Nothing writes
# menu.xml or history.tsv any more, but an account directory made before that
# still has them, and a blocklist entry costs nothing where a stray link would
# make one account read another's.
BLOCKLIST = {".credentials.json", ".claude.json", "menu.xml", "history.tsv",
             USAGE_FILE}


def _env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def claude_home() -> Path:
    return _env("CCAS_HOME", Path.home() / ".claude")


def claude_config_json() -> Path:
    return _env("CCAS_CLAUDE_JSON", Path.home() / ".claude.json")


def accounts_root() -> Path:
    return _env("CCAS_ACCOUNTS_ROOT", Path.home() / ".cc-accounts")


def registry_file() -> Path:
    return accounts_root() / "accounts.json"


def account_dir(slug: str) -> Path:
    return accounts_root() / slug


def projects_root() -> Path:
    return claude_home() / "projects"


def trash_dir() -> Path:
    return _env("CCAS_TRASH", Path.home() / ".claude_trash")


def waybar_config() -> Path:
    return _env("CCAS_WAYBAR_CONFIG", Path.home() / ".config/waybar/config.jsonc")


def bashrc() -> Path:
    return _env("CCAS_BASHRC", Path.home() / ".bashrc")


def menu_css() -> Path:
    """The GTK panel's stylesheet. install.sh ships it once and never overwrites
    it — the same stance taken toward style.css being the user's."""
    return _env("CCAS_MENU_CSS", Path.home() / ".config" / "ccas" / "menu.css")


def panel_output():
    """Which output the panel opens on, or None for the compositor's choice.

    A layer surface with no monitor set lands wherever the compositor decides,
    which on a two-head setup is not reliably the one Waybar is on. The
    connector name is the user's hardware ("HDMI-A-1"), so it is configuration
    rather than something CCAS may hardcode.
    """
    return os.environ.get("CCAS_PANEL_OUTPUT") or None


def panel_lock() -> Path:
    """Where the open panel records its pid, so a second click can close it.

    The runtime dir, not a config dir: it holds a pid, which means nothing after
    the session that owns it ends. Falls back to /tmp for the login that has no
    XDG_RUNTIME_DIR — a stale file there is handled the same way a stale pid is.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return _env("CCAS_PANEL_LOCK", Path(runtime) / "ccas-panel.lock")


def ccs_bin() -> Path:
    return _env("CCAS_CCS_BIN", Path.home() / ".local/bin/ccs")


def claude_bin() -> Path:
    return _env("CCAS_CLAUDE_BIN", Path.home() / ".local/bin/claude")
