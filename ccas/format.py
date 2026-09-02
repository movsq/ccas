"""The custom display mode: a format string, its tokens, and their colours.

A pure function of the account dict and the usage reading — no I/O, no
registry, and above all no GTK: `ccs statusline` runs in every prompt of every
session and reaches this module through label.render().

The format string is layout, never markup. Its literal text is escaped and the
only colours that reach the pango are the three validated shapes valid_color
accepts, so nothing a user types can produce a span attribute.
"""
import colorsys
import random
import re
import time

# The module, not its names: this needs six of them, and `usage` is what the
# reading itself is called at every call site here.
from . import paths, usage as usage_mod
from .label import ICON_RISE, ICON_SIZE, TEXT_SIZE, pango_escape

# What a token with no colour of its own renders as. Plain white, and the same
# answer for every token: `auto` used to make that answer depend on which token
# it was — a usage ramp here, the account's colour there — so the one thing a
# format string could not tell you was what it would look like. Two ways to say
# "no colour in particular" is one too many.
DEFAULT_COLOR = "#ffffff"

# Grey rather than the ramp's dim alpha: an alpha is a *dimmer* of whatever is
# underneath, so it could not survive `dim` being retired as a colour.
GREY = "#9399b2"

# The glyph is not in it: it is the widget's own mark, drawn by render()
# ahead of whatever the format asks for, and hidden only by hide_icon.
DEFAULT_FORMAT = "%email %5hused"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_format_colors(color: str) -> dict:
    """A new account's token colours. The used percentage takes the account's
    own colour so that the glyph and the number match from the first paint;
    the address is grey between them.

    A function rather than a constant because that hex differs per account.
    It used to be ACCOUNT — a stored indirection that kept the pair together
    when the hue moved, at the price of a colour setting whose effect you
    could not see from the format string. One drag per token is the cost of
    being able to read it.
    """
    return {"%email": GREY, "%5hused": color}


def valid_color(value) -> bool:
    """The one shape that may reach a pango attribute. This is what makes the
    format string layout rather than markup — nothing else a user types ever
    lands inside a span tag.

    A hex or nothing, with nothing meaning DEFAULT_COLOR. `auto` made the
    answer depend on which token asked; `account` made it depend on whether
    any token asked at all, so a colour control could be live, previewed and
    entirely without effect. Both are gone and neither comes back.
    """
    return bool(isinstance(value, str) and HEX.match(value))


def random_color() -> str:
    """A colour for a new account: any hue, at the palette's own lightness and
    a saturation inside the band its eight colours occupy.

    Random rather than the next free palette entry, because the palette stopped
    being the set of possible colours the moment the sliders arrived — with
    eight of them and two accounts, "the next free one" was just a fixed pair.
    """
    return hs_to_hex(random.uniform(0, 360), random.uniform(0.55, 0.92))


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
    """The token's colour, resolved. An unset one — and a stored `auto` or
    `dim` from before they were retired — is DEFAULT_COLOR; nothing migrates."""
    value = (account.get("format_colors") or {}).get(token)
    return value if valid_color(value) else DEFAULT_COLOR


def _icon(ctx) -> str:
    """The ✻, or nothing. Not a token — the widget's own mark, in the account's
    colour, with no per-token entry left to override it.

    hide_icon removes it outright rather than drawing it at alpha='1'. The
    invisible span was a spacer for a glyph the format string had asked for;
    with the glyph implicit in every label, keeping it would reserve the
    glyph's width in exactly the labels that asked not to have one.
    """
    if ctx["account"]["hide_icon"]:
        return ""
    color = ctx["account"]["color"]
    # Not pango_escape'd: the glyph carries its own measured size and rise, and
    # is the one piece of the label that is markup by nature.
    return (f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' "
            f"color='{color}'>{paths.GLYPH}</span>")


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


# What the clock slots say when there is no clock. IDLE is a measurement — the
# window is not running — so it gets a word; ABSENT is ignorance and keeps the
# empty string, because "idle" there would be a claim we cannot make.
def _no_clock(ctx, st):
    """What stands in a clock slot for a window that has no reset to show.

    Three answers, and they are three different facts. IDLE is a measurement —
    the window is not running — and says so in a word, because an empty slot
    left `%5hreset %5hquotaleft` collapsed to a bare `100%`, which on a usage
    widget reads as 100% *used*. STALLED is the absence of one, and names which
    stop it was: "logged out" and "rate limited" want different things done
    about them. ABSENT keeps the empty string, where any word would be a claim
    the reading cannot support.
    """
    if st.kind == usage_mod.STALLED:
        return usage_mod.stall_word(ctx["usage"])
    return usage_mod.IDLE_MARK if st.kind == usage_mod.IDLE else ""


def _reset(key, absolute):
    def text(ctx):
        st = _state(ctx, key)
        if st.kind != usage_mod.BOUNDED:
            return _no_clock(ctx, st)
        return usage_mod.reset_time(st.resets_at, _now(ctx)) if absolute \
            else usage_mod.reset_clock(st.resets_at)
    return text


def _reset_part(key, fmt_string):
    """Half of a reset clock: the weekday, or the time of day.

    `%7dreset` is one field, so it can only be placed and coloured as one. A
    label that wants "Mon" dim and "06:00" bright, or the day and the clock at
    opposite ends of the widget, needs two tokens. The weekday is unconditional
    here where `_reset(absolute=True)` drops it for a reset later today — a
    slot that empties itself moves everything beside it.
    """
    def text(ctx):
        st = _state(ctx, key)
        if st.kind != usage_mod.BOUNDED:
            return _no_clock(ctx, st)
        return time.strftime(fmt_string, time.localtime(st.resets_at))
    return text


def _timeleft_token(key):
    def text(ctx):
        st = _state(ctx, key)
        if st.kind != usage_mod.BOUNDED:
            return _no_clock(ctx, st)
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


def _smart(ctx):
    return (usage_mod.bar(ctx["usage"], ctx["now"]) or ("", ""))[0]


# token -> text_fn(ctx) -> str
# Every entry is a text function, wrapped in a TEXT_SIZE span by _emit. The
# glyph is not here: it returns finished markup and is drawn by render() rather
# than placed by the format string.
#
# One function per token, not two: the second used to be the token's `auto`
# colour — the usage ramp for the windowed ones, nothing for the rest — and
# with auto retired a token's colour comes from the account dict alone.
TOKENS = {
    "%name": lambda ctx: ctx["account"].get("nickname") or "",
    "%email": lambda ctx: ctx["account"]["email"],
    "%index": lambda ctx: str(ctx["index"]),

    "%5hreset": _reset("five_hour", False),
    "%5htimeleft": _timeleft_token("five_hour"),
    "%5hused": _percent("five_hour", False),
    "%5hquotaleft": _percent("five_hour", True),
    # %5h and %7d share _smart: usage.bar() already decides between the windows,
    # so having both names is a convenience for reading the format string, not
    # two behaviours.
    "%5h": _smart,

    "%7dreset": _reset("seven_day", True),
    "%7dresetday": _reset_part("seven_day", "%a"),
    "%7dresettime": _reset_part("seven_day", "%H:%M"),
    "%7dtimeleft": _timeleft_token("seven_day"),
    "%7dused": _percent("seven_day", False),
    "%7dquotaleft": _percent("seven_day", True),
    "%7d": _smart,

    # The model-scoped weekly window, shaped like the 7-day set because that is
    # what it is: a week's quota with a reset days out. Every one of them
    # renders empty for an account the endpoint reports no such window for —
    # ABSENT, not idle — so one format string can be shared by an account that
    # has a Fable limit and one that does not, and _collapse closes the gap.
    "%fablereset": _reset(usage_mod.FABLE, True),
    "%fableresetday": _reset_part(usage_mod.FABLE, "%a"),
    "%fableresettime": _reset_part(usage_mod.FABLE, "%H:%M"),
    "%fabletimeleft": _timeleft_token(usage_mod.FABLE),
    "%fableused": _percent(usage_mod.FABLE, False),
    "%fablequotaleft": _percent(usage_mod.FABLE, True),
}


# token -> what it is, for anything that shows a token to the user: the panel's
# colour chips and the two token tables. A token is the spelling you type into a
# format string, and the chip row labelled with one read as syntax rather than
# as the parts of the label it colours.
#
# Its keys are TOKENS' keys — a test pins that, so a token cannot ship nameless.
# %5h and %7d share a name because they share _smart: usage.bar() picks the
# window, so naming them apart would invent a difference.
NAMES = {
    "%name": "nickname",
    "%email": "email",
    "%index": "index",

    "%5hreset": "5h reset",
    "%5htimeleft": "5h remaining",
    "%5hused": "5h used",
    "%5hquotaleft": "5h left",
    "%5h": "usage clock",

    "%7dreset": "7d reset",
    "%7dresetday": "7d reset day",
    "%7dresettime": "7d reset time",
    "%7dtimeleft": "7d remaining",
    "%7dused": "7d used",
    "%7dquotaleft": "7d left",
    "%7d": "usage clock",

    "%fablereset": "Fable reset",
    "%fableresetday": "Fable reset day",
    "%fableresettime": "Fable reset time",
    "%fabletimeleft": "Fable remaining",
    "%fableused": "Fable used",
    "%fablequotaleft": "Fable left",
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
    text = TOKENS[token](ctx)
    if not text:
        return ""
    attrs = f"color='{_chosen(ctx['account'], token)}'"
    return f"<span size='{TEXT_SIZE}' {attrs}>{pango_escape(text)}</span>"


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
    # The glyph leads, ahead of anything the format string asks for: it is the
    # widget's identity rather than a piece of its layout.
    body = _collapse(pieces)
    glyph = _icon(ctx)
    if not glyph:
        return body
    return f"{glyph} {body}" if body else glyph


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
