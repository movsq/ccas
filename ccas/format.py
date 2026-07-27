"""The custom display mode: a format string, its tokens, and their colours.

A pure function of the account dict and the usage reading — no I/O, no
registry, and above all no GTK: `ccs statusline` runs in every prompt of every
session and reaches this module through label.render().

The format string is layout, never markup. Its literal text is escaped and the
only colours that reach the pango are the three validated shapes valid_color
accepts, so nothing a user types can produce a span attribute.
"""
import colorsys
import re
import time

# The module, not its names: this needs six of them, and `usage` is what the
# reading itself is called at every call site here.
from . import paths, usage as usage_mod
from .label import ICON_RISE, ICON_SIZE, TEXT_SIZE, pango_escape

AUTO, DIM, ACCOUNT = "auto", "dim", "account"

DEFAULT_FORMAT = "%icon %name %5h"
DEFAULT_FORMAT_COLORS = {}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def valid_color(value) -> bool:
    """The only four shapes that may reach a pango attribute. This is what
    makes the format string layout rather than markup — nothing else a user
    types ever lands inside a span tag."""
    return value in (AUTO, DIM, ACCOUNT) or \
        bool(isinstance(value, str) and HEX.match(value))


# Measured from paths.PALETTE, not chosen: its eight colours span 73.3%-86.1%
# lightness (mean 78.2%) while their saturation runs 54.1%-92.0%. The palette is
# already a fixed-lightness hue ring, so hue and saturation are its own
# coordinates and lightness is the axis nobody was using. Pinning it also means
# no slider position can reach black or white, which on a bar label is the
# point: saturation 0 is a grey that still reads on #353535.
LIGHTNESS = 0.78


def hex_to_hs(value: str) -> tuple:
    """(hue in degrees, saturation 0-1). The stored lightness is discarded —
    the editor shows the colour as it is and only snaps it on the first drag."""
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, _lightness, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, sat


def hs_to_hex(hue: float, saturation: float, lightness: float = LIGHTNESS) -> str:
    """The slider pair as a colour. Hue wraps because the slider is a ring;
    saturation clamps because a Gtk.Adjustment can overshoot its bounds."""
    saturation = max(0.0, min(1.0, saturation))
    rgb = colorsys.hls_to_rgb((hue % 360) / 360, lightness, saturation)
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb)


def _chosen(account: dict, token: str) -> str:
    value = (account.get("format_colors") or {}).get(token, AUTO)
    return value if valid_color(value) else AUTO


def _icon(ctx) -> str:
    # hide_icon deliberately wins over a colour override: it is the invisibility
    # toggle, and a colour that resurrected the glyph would make the panel's
    # checkbox lie.
    chosen = _chosen(ctx["account"], "%icon")
    if ctx["account"]["hide_icon"]:
        attrs = "alpha='1'"
    elif chosen in (AUTO, ACCOUNT):
        # For the glyph the two mean the same thing, so they agree rather
        # than compete.
        attrs = f"color='{ctx['account']['color']}'"
    elif chosen == DIM:
        attrs = f"alpha='{usage_mod.DIM}'"
    else:
        attrs = f"color='{chosen}'"
    # Not pango_escape'd and not wrapped by the caller: the glyph carries its own
    # measured size and rise, and is the one token that is markup by nature.
    return f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' {attrs}>{paths.GLYPH}</span>"


def _state(ctx, key):
    return usage_mod.state(ctx["usage"], key, ctx["now"])


def _timeleft(seconds: float) -> str:
    """d/h/m, two units at most: '2d21h', '2h20m', '48m'."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _now(ctx) -> float:
    return time.time() if ctx["now"] is None else ctx["now"]


def _reset(key, absolute):
    def text(ctx):
        st = _state(ctx, key)
        if st.kind != usage_mod.BOUNDED:
            return ""
        return usage_mod.reset_time(st.resets_at, _now(ctx)) if absolute \
            else usage_mod.reset_clock(st.resets_at)
    return text


def _timeleft_token(key):
    def text(ctx):
        st = _state(ctx, key)
        if st.kind != usage_mod.BOUNDED:
            return ""
        return _timeleft(st.resets_at - _now(ctx))
    return text


def _percent(key, remaining):
    def text(ctx):
        st = _state(ctx, key)
        if st.percent is None:
            return ""
        value = 100 - st.percent if remaining else st.percent
        return f"{value:.0f}%"
    return text


def _ramp(key):
    """The auto colour for a usage token: the ramp, or dim below it. The same
    decision usage.bar() makes, so a hand-written format and the smart token
    agree about pressure."""
    def attrs(ctx):
        st = _state(ctx, key)
        if st.percent is None:
            return ""
        value = usage_mod.color(st.percent)
        return f"color='{value}'" if value else f"alpha='{usage_mod.DIM}'"
    return attrs


def _smart(ctx):
    return (usage_mod.bar(ctx["usage"], ctx["now"]) or ("", ""))[0]


def _smart_attrs(ctx):
    return (usage_mod.bar(ctx["usage"], ctx["now"]) or ("", ""))[1]


# token -> (text_fn(ctx) -> str, auto_attrs_fn(ctx) -> str)
# The text is plain and gets wrapped in a TEXT_SIZE span by _emit; %icon is the
# exception and returns finished markup, flagged by the None pair below.
TOKENS = {
    "%icon": (None, None),
    "%name": (lambda ctx: ctx["account"].get("nickname") or "", lambda ctx: ""),
    "%email": (lambda ctx: ctx["account"]["email"], lambda ctx: ""),
    "%index": (lambda ctx: str(ctx["index"]), lambda ctx: ""),

    "%5hreset": (_reset("five_hour", False), _ramp("five_hour")),
    "%5htimeleft": (_timeleft_token("five_hour"), _ramp("five_hour")),
    "%5hused": (_percent("five_hour", False), _ramp("five_hour")),
    "%5hquotaleft": (_percent("five_hour", True), _ramp("five_hour")),
    # %5h and %7d share _smart: usage.bar() already decides between the windows,
    # so having both names is a convenience for reading the format string, not
    # two behaviours.
    "%5h": (_smart, _smart_attrs),

    "%7dreset": (_reset("seven_day", True), _ramp("seven_day")),
    "%7dtimeleft": (_timeleft_token("seven_day"), _ramp("seven_day")),
    "%7dused": (_percent("seven_day", False), _ramp("seven_day")),
    "%7dquotaleft": (_percent("seven_day", True), _ramp("seven_day")),
    "%7d": (_smart, _smart_attrs),
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
    chosen = _chosen(ctx["account"], token)
    if chosen == AUTO:
        attrs = auto_fn(ctx)
    elif chosen == DIM:
        attrs = f"alpha='{usage_mod.DIM}'"
    elif chosen == ACCOUNT:
        attrs = f"color='{ctx['account']['color']}'"
    else:
        attrs = f"color='{chosen}'"
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
