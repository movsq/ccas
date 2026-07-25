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

DISPLAY_MODES = ["nickname", "index", "claude code", "icon only"]

# How many recent sessions the resume picker lists. fuzzel scrolls, so this is
# a bound on the scan, not on what fits on screen.
HIST_SLOTS = 300

BLOCKLIST = {".credentials.json", ".claude.json", "menu.xml", "history.tsv"}


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


def ccs_bin() -> Path:
    return _env("CCAS_CCS_BIN", Path.home() / ".local/bin/ccs")


def claude_bin() -> Path:
    return _env("CCAS_CLAUDE_BIN", Path.home() / ".local/bin/claude")
