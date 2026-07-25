"""Rendering the Waybar label for one account."""
from xml.sax.saxutils import escape

from . import paths
# The function, not the module: `usage` is what the reading is called at every
# call site, and the parameter should be able to keep that name.
from .usage import bar as usage_bar

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
    """The bar label: the glyph, whatever the display mode asks for, and — when
    the 5-hour window is bounded — the clock it clears at, coloured by pressure.

    `usage` is a reading from usage.read(); None renders the bar as it was
    before the feature existed, which is also what an open window renders as.
    """
    color = paths.PALETTE[account["color"]][1]
    if account["hide_icon"]:
        icon = f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' alpha='1'>{paths.GLYPH}</span>"
    else:
        icon = f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' color='{color}'>{paths.GLYPH}</span>"

    token = usage_bar(usage, now)
    token = (f"<span size='{TEXT_SIZE}' {token[1]}>{pango_escape(token[0])}</span>"
             if token else "")

    mode = account["display"]
    if mode == "icon only":
        return f"{icon} {token}" if token else icon
    if mode == "index":
        text = str(index)
    elif mode == "claude code":
        text = "claude code"
    else:
        # Deliberately not display_name(): a cleared nickname means "show no
        # text", so the bar falls back to the bare glyph rather than to a long
        # email address. display_name keeps its fallback for the menu title and
        # the account chooser, where a blank row would be unpickable.
        text = account.get("nickname") or ""
    if text:
        return " ".join(filter(None, [
            icon, f"<span size='{TEXT_SIZE}'>{pango_escape(text)}</span>", token]))
    return f"{icon} {token}" if token else icon


def check_invisible_warning(account: dict) -> bool:
    """True when the caller should notify. Latches, and re-arms on exit."""
    invisible = account["hide_icon"] and account["display"] == "icon only"
    if not invisible:
        account["warned_invisible"] = False
        return False
    if account["warned_invisible"]:
        return False
    account["warned_invisible"] = True
    return True
