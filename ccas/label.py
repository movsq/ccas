"""Rendering the Waybar label for one account."""
from xml.sax.saxutils import escape

from . import paths

ICON_SIZE = "x-large"
# The glyph sits high in the font, so at x-large it reads as floating above the
# text beside it. rise is pango's baseline shift, in 1024ths of a point.
ICON_RISE = "-800"
# The bar's 11px text renders a cap one pixel shorter than the enlarged glyph,
# which reads as the glyph floating beside it rather than sitting with it.
TEXT_SIZE = "110%"

WARNING_TEXT = "it's still there — invisible."


def pango_escape(text: str) -> str:
    return escape(str(text))


def display_name(account: dict) -> str:
    return account.get("nickname") or account["email"]


def render(account: dict, index: int) -> str:
    color = paths.PALETTE[account["color"]][1]
    if account["hide_icon"]:
        icon = f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' alpha='1'>{paths.GLYPH}</span>"
    else:
        icon = f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' color='{color}'>{paths.GLYPH}</span>"

    mode = account["display"]
    if mode == "icon only":
        return icon
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
    return f"{icon} <span size='{TEXT_SIZE}'>{pango_escape(text)}</span>" if text else icon


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
