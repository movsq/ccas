"""Rendering the Waybar label for one account."""
from xml.sax.saxutils import escape

from . import paths

ICON_SIZE = "150%"
# The glyph sits high in the font, so at x-large it reads as floating above the
# text beside it. rise is pango's baseline shift, in 1024ths of a point.
ICON_RISE = "-800"
# The bar's 11px text renders a cap one pixel shorter than the enlarged glyph,
# which reads as the glyph floating beside it rather than sitting with it.
TEXT_SIZE = "110%"

WARNING_TEXT = "it's still there — invisible."

# Every stateful row marks itself with this pair, radio or toggle. Not ☑/☐: the
# bar's font stack here starts with FontAwesome, which covers U+2611 but not
# U+2610, so the checked box came from FontAwesome and the unchecked one from
# DejaVu — different sizes, different weights, and the "on" state looked like an
# empty box on the bar. U+25CF/U+25CB were checked against that exact stack with
# pango-view and render consistently.
MARK_ON = "●"
MARK_OFF = "○"


def pango_escape(text: str) -> str:
    return escape(str(text))


def display_name(account: dict) -> str:
    return account.get("nickname") or account["email"]


def render(account: dict, index: int, usage=None, now=None) -> str:
    """The bar label: whatever the account's format string asks for.

    There is one way to build a label. The four named modes this used to switch
    on were presets the format string already expressed — "icon only" is the
    string "%icon" — and the branch that chose between them was the reason the
    panel carried two colour rows that meant different things.

    `usage` is a reading from usage.read(), passed through.
    """
    # Imported here, not at module scope: format.py imports this module's
    # measured metrics, and a top-level import either way is a cycle.
    from . import format as fmt
    return fmt.render(account, index, usage, now)


def check_invisible_warning(account: dict) -> bool:
    """True when the caller should notify. Latches, and re-arms on exit.

    "Invisible" used to be the hidden glyph plus the "icon only" mode. With the
    modes retired the same combination is a format string that asks for the
    glyph and nothing else — the test moved from a mode name to the string,
    because that is where the answer now lives.
    """
    from . import format as fmt
    fmt_string = account.get("format") or fmt.DEFAULT_FORMAT
    invisible = account["hide_icon"] and not fmt_string.replace("%icon", "").strip()
    if not invisible:
        account["warned_invisible"] = False
        return False
    if account["warned_invisible"]:
        return False
    account["warned_invisible"] = True
    return True
