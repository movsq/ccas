"""The custom display mode: a format string, its tokens, and their colours.

A pure function of the account dict and the usage reading — no I/O, no
registry, and above all no GTK: `ccs statusline` runs in every prompt of every
session and reaches this module through label.render().

The format string is layout, never markup. Its literal text is escaped and the
only colours that reach the pango are the three validated shapes valid_color
accepts, so nothing a user types can produce a span attribute.
"""
import re

from . import paths
from .label import ICON_RISE, ICON_SIZE, TEXT_SIZE, pango_escape

DEFAULT_FORMAT = "%icon %name %5h"

AUTO, DIM = "auto", "dim"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _icon(ctx) -> str:
    color = paths.PALETTE[ctx["account"]["color"]][1]
    attrs = "alpha='1'" if ctx["account"]["hide_icon"] else f"color='{color}'"
    # Not pango_escape'd and not wrapped by the caller: the glyph carries its own
    # measured size and rise, and is the one token that is markup by nature.
    return f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' {attrs}>{paths.GLYPH}</span>"


# token -> (text_fn(ctx) -> str, auto_attrs_fn(ctx) -> str)
# The text is plain and gets wrapped in a TEXT_SIZE span by _emit; %icon is the
# exception and returns finished markup, flagged by the None pair below.
TOKENS = {
    "%icon": (None, None),
    "%name": (lambda ctx: ctx["account"].get("nickname") or "", lambda ctx: ""),
    "%email": (lambda ctx: ctx["account"]["email"], lambda ctx: ""),
    "%index": (lambda ctx: str(ctx["index"]), lambda ctx: ""),
}


# Longest first, so %5hused is never read as %5h followed by the text "used".
def _pattern():
    names = sorted(TOKENS, key=len, reverse=True)
    return re.compile("|".join([re.escape(n) for n in names] + [r"%%", r"%\w+"]))


def tokens_in(fmt: str) -> list:
    """The known tokens the format uses, in order, de-duplicated."""
    out = []
    for match in _pattern().finditer(fmt or ""):
        text = match.group(0)
        if text in TOKENS and text not in out:
            out.append(text)
    return out


def unknown_tokens(fmt: str) -> list:
    out = []
    for match in _pattern().finditer(fmt or ""):
        text = match.group(0)
        if text != "%%" and text not in TOKENS and text not in out:
            out.append(text)
    return out


def _emit(token: str, ctx) -> str:
    if token == "%icon":
        return _icon(ctx)
    text_fn, auto_fn = TOKENS[token]
    text = text_fn(ctx)
    if not text:
        return ""
    attrs = auto_fn(ctx)
    return f"<span size='{TEXT_SIZE}'{' ' + attrs if attrs else ''}>" \
           f"{pango_escape(text)}</span>"


def render(account: dict, index: int, usage=None, now=None,
           format_override=None) -> str:
    """The label for display mode "custom". format_override is for tests."""
    fmt = format_override if format_override is not None else \
        (account.get("format") or DEFAULT_FORMAT)
    ctx = {"account": account, "index": index, "usage": usage, "now": now}

    pos, pieces = 0, []
    for match in _pattern().finditer(fmt):
        pieces.append(("lit", fmt[pos:match.start()]))
        text = match.group(0)
        if text == "%%":
            pieces.append(("lit", "%"))
        elif text in TOKENS:
            pieces.append(("tok", _emit(text, ctx)))
        else:
            pieces.append(("lit", text))
        pos = match.end()
    pieces.append(("lit", fmt[pos:]))
    return _collapse(pieces)


def _collapse(pieces) -> str:
    """Join, dropping the whitespace that surrounded a token that came out
    empty — an unwired hook must leave no gap in the middle of the label."""
    out = []
    for kind, text in pieces:
        if kind == "tok" and not text:
            # Eat the trailing whitespace already emitted; the next literal's
            # leading whitespace then supplies the single separator.
            if out and out[-1].endswith(" ") and not out[-1].strip():
                out.pop()
            elif out:
                out[-1] = out[-1].rstrip()
            continue
        out.append(text)
    return "".join(out).strip()
