# Custom Display Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (The user has explicitly declined subagent-driven development for this repo — see CLAUDE.md.)

**Goal:** Add a fifth display mode, `custom`, whose bar label is a user-written format string of coloured tokens — and take the hardcoded usage clock out of the four built-in modes, so each of them finally means what it says.

**Architecture:** One new pure module, `ccas/format.py`, holding a token table and a renderer. It is a function of the account dict and the usage reading — no I/O, no GTK, no registry. `label.render()` keeps the four built-ins and delegates the fifth to it. The format string is set from the terminal (`ccs format`); per-token colour is set from the panel, which never parses a format string because `panel.build_state` hands it a precomputed token list.

**Tech Stack:** Python 3.14, stdlib only. pytest. Pango markup for the label. GTK4 via PyGObject in `panel_ui.py` only.

## Global Constraints

- **stdlib only at runtime.** No new dependencies, no build step.
- **Never write to `~/.claude`.** Nothing in this plan goes near it; the fields added live in `accounts.json`.
- **Never delete** — anything removed moves to `~/.claude_trash/`. Nothing in this plan deletes a file.
- **Tests must never touch real state.** Every path goes through `ccas/paths.py`; tests use the existing fixtures.
- **`gi` is imported inside `show()`**, never at module scope. `ccas/format.py` must import no GTK at all — `ccs statusline` runs in every prompt of every session and reaches `label.render`.
- **Never invoke `claude` by bare name.** Not touched here, but `_in_terminal` is, so it stays `paths.ccs_bin()`.
- **One commit per task.** Never co-sign or co-author.
- **`ccs` runs the installed copy.** Run `./install.sh` before any live check.
- Run the whole suite with `cd ~/ccas && python -m pytest` (~330 tests, under a second).

---

### Task 1: `ccas/format.py` — the parser and the identity tokens

**Files:**
- Create: `ccas/format.py`
- Create: `tests/test_format.py`

**Interfaces:**
- Produces: `DEFAULT_FORMAT`, `AUTO`, `DIM`, `TOKENS` (dict), `tokens_in(fmt) -> list[str]`, `unknown_tokens(fmt) -> list[str]`, `render(account, index, usage=None, now=None) -> str`
- Consumes: `label.ICON_SIZE`, `label.ICON_RISE`, `label.TEXT_SIZE`, `label.pango_escape`, `paths.GLYPH`, `paths.PALETTE`

- [ ] **Step 1: Write the failing test**

Create `tests/test_format.py`:

```python
import ccas.format as fmt

ICON = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": 1, "display": "custom", "hide_icon": False,
        "warned_invisible": False, "signal": 1,
        "format": fmt.DEFAULT_FORMAT, "format_colors": {},
    }
    base.update(kw)
    return base


def test_literal_text_passes_through():
    assert fmt.render(account(format="hello"), 1) == "hello"


def test_double_percent_is_a_literal_percent():
    assert fmt.render(account(format="100%%"), 1) == "100%"


def test_identity_tokens():
    a = account()
    assert fmt.render(a, 3, format_override="%name") == "<span size='110%'>work</span>"
    assert fmt.render(a, 3, format_override="%email") == "<span size='110%'>w@example.com</span>"
    assert fmt.render(a, 3, format_override="%index") == "<span size='110%'>3</span>"
    assert fmt.render(a, 3, format_override="%icon") == ICON


def test_icon_honours_hide_icon():
    """hide_icon keeps the glyph's width but drops its ink — the same alpha='1'
    trick label.render uses, so the bar does not jump when it is toggled."""
    out = fmt.render(account(hide_icon=True), 1, format_override="%icon")
    assert out == "<span size='150%' rise='-800' alpha='1'>✻</span>"


def test_a_cleared_nickname_renders_empty():
    """The rule label.render already keeps: a cleared nickname means "show no
    text", not "show my email address"."""
    assert fmt.render(account(nickname=None), 1, format_override="%name") == ""
    assert fmt.render(account(nickname=""), 1, format_override="%name") == ""


def test_text_is_escaped():
    assert fmt.render(account(nickname="a<b&c"), 1, format_override="%name") \
        == "<span size='110%'>a&lt;b&amp;c</span>"


def test_unknown_tokens_render_literally():
    """Never blank. A Waybar module that renders nothing is indistinguishable
    from a crashed one, so a typo must name itself on screen."""
    assert fmt.render(account(format="%bogus"), 1) == "%bogus"
    assert fmt.unknown_tokens("%name %bogus %nope") == ["%bogus", "%nope"]
    assert fmt.unknown_tokens("%name %5h") == []


def test_tokens_in_is_ordered_and_deduplicated():
    assert fmt.tokens_in("%icon %name %icon %5h") == ["%icon", "%name", "%5h"]


def test_empty_tokens_collapse_the_space_around_them():
    """An unwired hook must not leave a stray gap in the middle of the label."""
    assert fmt.render(account(nickname=None), 1, format_override="%icon %name %email") \
        == f"{ICON} <span size='110%'>w@example.com</span>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccas.format'`

- [ ] **Step 3: Write minimal implementation**

Create `ccas/format.py`:

```python
"""The custom display mode: a format string, its tokens, and their colours.

A pure function of the account dict and the usage reading — no I/O, no
registry, and above all no GTK: `ccs statusline` runs in every prompt of every
session and reaches this module through label.render().

The format string is layout, never markup. Its literal text is escaped and the
only colours that reach the pango are the three validated shapes in COLORS, so
nothing a user types can produce a span attribute.
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
# exception and returns finished markup, flagged by a None text_fn slot below.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: PASS, 8 tests.

If `test_empty_tokens_collapse_the_space_around_them` fails, `_collapse` is the
only suspect — it is the fiddly part. Simplify it to: build the string, then
`re.sub(r" {2,}", " ", joined).strip()`. That is equivalent for every format
that separates tokens with single spaces, which is all of them, and it is easier
to be sure of.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/format.py tests/test_format.py
git commit -m "Add the format module: the parser and the identity tokens"
```

---

### Task 2: The usage tokens

**Files:**
- Modify: `ccas/format.py` (the `TOKENS` table)
- Modify: `tests/test_format.py`

**Interfaces:**
- Consumes from Task 1: `TOKENS`, `_emit`, the `ctx` dict (`account`, `index`, `usage`, `now`)
- Consumes: `usage.state()`, `usage.bar()`, `usage.reset_clock()`, `usage.reset_time()`, `usage.BOUNDED`, `usage.OPEN`
- Produces: eight window tokens plus `%5h` / `%7d`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_format.py`:

```python
import ccas.usage as usage

NOW = 1_800_000_000.0            # a fixed epoch, so the clocks are stable
IN_2H20 = NOW + 2 * 3600 + 20 * 60


def reading(five_pct=62.0, five_at=IN_2H20, seven_pct=None, seven_at=None):
    out = {"fetched_at": NOW, "source": "statusline",
           "five_hour": None, "seven_day": None}
    if five_pct is not None:
        out["five_hour"] = {"percent": five_pct, "resets_at": int(five_at)}
    if seven_pct is not None:
        out["seven_day"] = {"percent": seven_pct, "resets_at": int(seven_at)}
    return out


def bare(account_kw, token, usage=None, now=NOW):
    """The token's text with its span stripped — the markup is asserted
    separately in the colour tests, and repeating it here would hide the fact
    being checked."""
    out = fmt.render(account(**account_kw), 1, usage, now, format_override=token)
    return re.sub(r"<[^>]+>", "", out)


def test_five_hour_tokens():
    r = reading()
    assert bare({}, "%5hreset", r) == usage.reset_clock(int(IN_2H20))
    assert bare({}, "%5htimeleft", r) == "2h20m"
    assert bare({}, "%5hused", r) == "62%"
    assert bare({}, "%5hquotaleft", r) == "38%"


def test_seven_day_tokens():
    """%7dreset is a weekday and a clock: a 7-day reset can be days out, and a
    bare HH:MM would be a lie about which day."""
    r = reading(seven_pct=100.0, seven_at=NOW + 2 * 86400 + 21 * 3600)
    assert bare({}, "%7dreset", r) == usage.reset_time(int(NOW + 2 * 86400 + 21 * 3600), NOW)
    assert bare({}, "%7dtimeleft", r) == "2d21h"
    assert bare({}, "%7dused", r) == "100%"
    assert bare({}, "%7dquotaleft", r) == "0%"


def test_timeleft_under_an_hour_drops_the_hours():
    assert bare({}, "%5htimeleft", reading(five_at=NOW + 48 * 60)) == "48m"


def test_an_absent_window_renders_every_one_of_its_tokens_empty():
    for token in ("%5hreset", "%5htimeleft", "%5hused", "%5hquotaleft", "%5h"):
        assert fmt.render(account(), 1, None, NOW, format_override=token) == ""


def test_an_open_window_has_no_clock_but_keeps_its_percentage():
    """resets_at in the past means the window rolled over, so the clock is
    meaningless — but the recorded percentage is still the last thing known."""
    r = reading(five_at=NOW - 60)
    assert bare({}, "%5hreset", r) == ""
    assert bare({}, "%5htimeleft", r) == ""
    assert bare({}, "%5hused", r) == "62%"


def test_the_smart_tokens_are_usage_bar():
    r = reading()
    assert bare({}, "%5h", r) == usage.bar(r, NOW)[0]
    seven = reading(five_pct=None, seven_pct=95.0, seven_at=NOW + 86400)
    assert bare({}, "%7d", seven) == "7d 95%"


def test_the_default_format_reproduces_todays_bar():
    """The migration canary. Stripping the clock from the built-in modes is only
    safe because '%icon %name %5h' puts back exactly what they used to show."""
    import ccas.label as label
    a = account(display="nickname")
    r = reading()
    assert fmt.render(a, 1, r, NOW) == label.render(dict(a, display="nickname"), 1, r, NOW) \
        or True   # replaced by the real assertion in Task 4, where render changes
```

Add `import re` at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: FAIL — `%5hreset` renders literally as `%5hreset` (Task 1's unknown-token behaviour), so `bare(...) == "13:40"` fails.

- [ ] **Step 3: Write minimal implementation**

In `ccas/format.py`, import usage and add the tokens. `usage` is imported as a
module here, not as `bar` — this module needs six of its names:

```python
from . import paths, usage as usage_mod
```

Add above `TOKENS`:

```python
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
    import time
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
```

Then extend the table:

```python
TOKENS = {
    "%icon": (None, None),
    "%name": (lambda ctx: ctx["account"].get("nickname") or "", lambda ctx: ""),
    "%email": (lambda ctx: ctx["account"]["email"], lambda ctx: ""),
    "%index": (lambda ctx: str(ctx["index"]), lambda ctx: ""),

    "%5hreset": (_reset("five_hour", False), _ramp("five_hour")),
    "%5htimeleft": (_timeleft_token("five_hour"), _ramp("five_hour")),
    "%5hused": (_percent("five_hour", False), _ramp("five_hour")),
    "%5hquotaleft": (_percent("five_hour", True), _ramp("five_hour")),
    "%5h": (_smart, _smart_attrs),

    "%7dreset": (_reset("seven_day", True), _ramp("seven_day")),
    "%7dtimeleft": (_timeleft_token("seven_day"), _ramp("seven_day")),
    "%7dused": (_percent("seven_day", False), _ramp("seven_day")),
    "%7dquotaleft": (_percent("seven_day", True), _ramp("seven_day")),
    "%7d": (_smart, _smart_attrs),
}
```

Note `%5h` and `%7d` share `_smart`: `usage.bar()` already decides between the
windows, and having both tokens is a convenience for reading the format, not two
behaviours.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: PASS. If `%5htimeleft` reads `2h20m` where the code emits `2h20m` —
check `_timeleft`'s `{minutes:02d}`; the test expects `2h20m`, so 20 formats as
`20` either way, but 5 minutes must read `2h05m`. Add that case if it is not
already covered.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/format.py tests/test_format.py
git commit -m "Add the usage tokens: four per window, plus the smart pair"
```

---

### Task 3: Per-token colour

**Files:**
- Modify: `ccas/format.py`
- Modify: `tests/test_format.py`

**Interfaces:**
- Produces: `valid_color(value) -> bool`, and `_emit` honouring `account["format_colors"]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_format.py`:

```python
def test_auto_is_the_default_and_means_the_tokens_own_colour():
    r = reading(five_pct=96.0)
    out = fmt.render(account(), 1, r, NOW, format_override="%5hused")
    assert f"color='{usage.RED}'" in out


def test_a_named_colour_overrides_auto():
    r = reading(five_pct=96.0)
    a = account(format_colors={"%5hused": "#89b4fa"})
    out = fmt.render(a, 1, r, NOW, format_override="%5hused")
    assert "color='#89b4fa'" in out
    assert usage.RED not in out


def test_dim_is_an_alpha_not_a_colour():
    a = account(format_colors={"%name": "dim"})
    assert fmt.render(a, 1, format_override="%name") \
        == f"<span size='110%' alpha='{usage.DIM}'>work</span>"


def test_an_explicit_auto_is_the_same_as_absent():
    r = reading(five_pct=96.0)
    a = account(format_colors={"%5hused": "auto"})
    assert fmt.render(a, 1, r, NOW, format_override="%5hused") \
        == fmt.render(account(), 1, r, NOW, format_override="%5hused")


def test_the_icon_takes_an_override_too():
    a = account(format_colors={"%icon": "#a6e3a1"})
    assert fmt.render(a, 1, format_override="%icon") \
        == "<span size='150%' rise='-800' color='#a6e3a1'>✻</span>"


def test_valid_color():
    assert fmt.valid_color("auto") and fmt.valid_color("dim")
    assert fmt.valid_color("#f9e2af") and fmt.valid_color("#F9E2AF")
    assert not fmt.valid_color("f9e2af")
    assert not fmt.valid_color("red")
    assert not fmt.valid_color("#f9e2a")
    assert not fmt.valid_color("' foreground='x")   # no attribute injection
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: FAIL — `AttributeError: module 'ccas.format' has no attribute 'valid_color'`, and the override tests fail because `_emit` ignores `format_colors`.

- [ ] **Step 3: Write minimal implementation**

In `ccas/format.py`:

```python
def valid_color(value) -> bool:
    """The only three shapes that may reach a pango attribute. This is what
    makes the format string layout rather than markup — nothing else a user
    types ever lands inside a span tag."""
    return value in (AUTO, DIM) or bool(isinstance(value, str) and HEX.match(value))


def _chosen(account: dict, token: str) -> str:
    value = (account.get("format_colors") or {}).get(token, AUTO)
    return value if valid_color(value) else AUTO
```

Rewrite `_emit` and `_icon` to consult it:

```python
def _icon(ctx) -> str:
    chosen = _chosen(ctx["account"], "%icon")
    if ctx["account"]["hide_icon"]:
        attrs = "alpha='1'"
    elif chosen == AUTO:
        attrs = f"color='{paths.PALETTE[ctx['account']['color']][1]}'"
    elif chosen == DIM:
        attrs = f"alpha='{usage_mod.DIM}'"
    else:
        attrs = f"color='{chosen}'"
    return f"<span size='{ICON_SIZE}' rise='{ICON_RISE}' {attrs}>{paths.GLYPH}</span>"


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
    else:
        attrs = f"color='{chosen}'"
    return f"<span size='{TEXT_SIZE}'{' ' + attrs if attrs else ''}>" \
           f"{pango_escape(text)}</span>"
```

`hide_icon` deliberately wins over a colour override: it is the invisibility
toggle, and a colour that resurrected the glyph would make the checkbox lie.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest tests/test_format.py -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/format.py tests/test_format.py
git commit -m "Colour each token: auto, dim, or a hex, validated at the boundary"
```

---

### Task 4: `label.py` delegates, and the built-in modes lose the clock

**Files:**
- Modify: `ccas/label.py:37-70`
- Modify: `tests/test_label.py`
- Modify: `tests/test_format.py` (finish the canary from Task 2)

**Interfaces:**
- Consumes: `format.render(account, index, usage, now)`
- Produces: `label.render` unchanged in signature; `display == "custom"` delegates

- [ ] **Step 1: Write the failing test**

In `tests/test_label.py`, add:

```python
def test_the_built_in_modes_no_longer_carry_a_usage_token():
    """The bug this feature exists for: "icon only" was an icon and a clock, and
    no mode could ask for the icon alone. Usage now lives in "custom" only."""
    r = {"fetched_at": 0, "source": "statusline",
         "five_hour": {"percent": 62.0, "resets_at": 1_800_008_400},
         "seven_day": None}
    now = 1_800_000_000.0
    icon = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"
    assert label.render(account(display="icon only"), 1, r, now) == icon
    assert label.render(account(), 1, r, now) == f"{icon} <span size='110%'>work</span>"
    assert label.render(account(display="index"), 3, r, now) == f"{icon} <span size='110%'>3</span>"


def test_custom_delegates_to_the_format_module():
    import ccas.format as fmt
    a = account(display="custom", format="%name %email")
    assert label.render(a, 1) == fmt.render(a, 1)
```

Then delete the now-wrong existing assertions in `test_label.py` that expect a
clock beside a built-in mode. Find them with:

```bash
cd ~/ccas && grep -n "usage\|resets_at" tests/test_label.py
```

Every test there that pairs a reading with a built-in display mode is asserting
the old behaviour. Keep the ones that test `usage.bar` itself (those belong to
`test_usage.py` and should already be there); delete the ones asserting the
combined label, and note in the docstring of the new test that they were removed
deliberately.

In `tests/test_format.py`, replace the placeholder canary from Task 2 with the
real one:

```python
def test_the_default_format_reproduces_the_old_bar():
    """The migration canary, spelled out rather than compared against
    label.render — which no longer produces this, by design."""
    icon = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"
    r = reading()
    clock = usage.reset_clock(int(IN_2H20))
    assert fmt.render(account(), 1, r, NOW) == \
        f"{icon} <span size='110%'>work</span> " \
        f"<span size='110%' alpha='{usage.DIM}'>{clock}</span>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_label.py tests/test_format.py -x`
Expected: FAIL — `icon only` still renders the clock beside the glyph.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `label.render` (`ccas/label.py:37-70`) with:

```python
def render(account: dict, index: int, usage=None, now=None) -> str:
    """The bar label: the glyph and whatever the display mode asks for.

    The four built-in modes are exactly what they are named — "icon only" is an
    icon, and nothing else. Usage lives in the "custom" mode's tokens, which is
    the only place it can be asked for, moved, coloured or left out.
    """
    if account["display"] == "custom":
        # Imported here, not at module scope: format.py imports this module's
        # measured metrics, and a top-level import either way is a cycle.
        from . import format as fmt
        return fmt.render(account, index, usage, now)

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
    if text:
        return f"{icon} <span size='{TEXT_SIZE}'>{pango_escape(text)}</span>"
    return icon
```

The `from .usage import bar as usage_bar` import at the top of `label.py` is now
unused — remove it, along with the comment above it explaining the aliasing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS, whole suite. Other modules' tests may reference the old combined
label — fix any that do the same way as `test_label.py`.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/label.py tests/test_label.py tests/test_format.py
git commit -m "Take the clock out of the built-in modes, and delegate the custom one"
```

---

### Task 5: The registry — the fifth mode, the two fields, their validation

**Files:**
- Modify: `ccas/paths.py:18`
- Modify: `ccas/registry.py:78-79` (defaults), `:117-125` (`set_field`)
- Modify: `tests/test_registry.py`

**Interfaces:**
- Produces: `paths.DISPLAY_MODES` with `"custom"`; accounts carrying `format` and `format_colors`; `set_field` validating both

- [ ] **Step 1: Write the failing test**

In `tests/test_registry.py`:

```python
import ccas.format as fmt


def test_custom_is_a_display_mode():
    assert "custom" in paths.DISPLAY_MODES
    assert paths.DISPLAY_MODES[-1] == "custom"   # appended, so no index moved


def test_a_new_account_carries_the_default_format():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    account = registry.find(reg, "work")
    assert account["format"] == fmt.DEFAULT_FORMAT
    assert account["format_colors"] == {}


def test_set_field_validates_the_format():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format", "%icon %5hused")
    assert registry.find(reg, "work")["format"] == "%icon %5hused"
    with pytest.raises(ValueError):
        registry.set_field(reg, "work", "format", 7)


def test_an_unknown_token_is_stored_not_rejected():
    """Rejecting would mean that retiring a token in a later version turns a
    stored format into a hard error with nothing on the bar. Rendering it
    literally turns the same event into visible, self-explaining text."""
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format", "%name %bogus")
    assert registry.find(reg, "work")["format"] == "%name %bogus"


def test_set_field_validates_the_colours():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format_colors", {"%name": "#f9e2af"})
    for bad in ({"%name": "red"}, {"%name": "' x='y"}, "not a dict"):
        with pytest.raises(ValueError):
            registry.set_field(reg, "work", "format_colors", bad)
```

`tests/test_registry.py` has an autouse `_isolate` fixture that points
`CCAS_ACCOUNTS_ROOT` at a `tmp_path` and reloads both modules, so
`registry.load()` on an empty root returns `{"default": None, "accounts": []}` —
that is the blank registry, and `registry.add(reg, slug, email, nickname)` takes
four arguments.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_registry.py -x`
Expected: FAIL — `"custom" not in paths.DISPLAY_MODES`.

- [ ] **Step 3: Write minimal implementation**

`ccas/paths.py:18`:

```python
DISPLAY_MODES = ["nickname", "index", "claude code", "icon only", "custom"]
```

Appended, not inserted: `ccs display <slug> <mode>` and the panel dropdown both
address modes by position in this list in at least one place, and inserting would
silently repoint existing selections.

In `ccas/registry.py`, beside `"hide_icon": False` in the new-account dict:

```python
        "format": format.DEFAULT_FORMAT,
        "format_colors": {},
```

with `from . import format` at the top — `registry` does not import `label`, and
`format` imports `label` not `registry`, so there is no cycle.

In `set_field`, beside the two checks already there:

```python
    if field == "format" and not isinstance(value, str):
        raise ValueError(f"format must be a string: {value!r}")
    if field == "format_colors":
        if not isinstance(value, dict) or \
                not all(format.valid_color(v) for v in value.values()):
            raise ValueError(f"invalid format colours: {value!r}")
```

An unknown *token* is deliberately not checked here — see the test's docstring.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS. Existing accounts in a test fixture that lack the two keys must
still work — `format.render` already defaults both with `.get`, and
`test_format.py`'s account helper should have one test built without them:

```python
def test_an_account_predating_the_feature_still_renders():
    a = account()
    del a["format"], a["format_colors"]
    assert fmt.render(a, 1) == fmt.render(account(), 1)
```

Add that to `tests/test_format.py` in this task.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/paths.py ccas/registry.py tests/test_registry.py tests/test_format.py
git commit -m "Register the custom mode and its two fields, validated at set_field"
```

---

### Task 6: `ccs format`, and the doctor check

**Files:**
- Modify: `ccas/cli.py` (a `cmd_format`, and a `format` branch in `main`)
- Modify: `ccas/doctor.py` (a check in `_account_checks`)
- Modify: `tests/test_cli.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `cli._mutate(slug, field, value)`, `format.tokens_in`, `format.unknown_tokens`, `format.valid_color`, `format.TOKENS`, `format.DEFAULT_FORMAT`
- Produces: `cli.cmd_format(rest) -> int`

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py` (following the file's existing fixture style for a
registry and captured stdout):

```python
def test_format_shows_the_current_format(capsys, reg_with_work):
    assert cli.main(["format", "work"]) == 0
    out = capsys.readouterr().out
    assert fmt.DEFAULT_FORMAT in out


def test_format_sets_it(reg_with_work):
    assert cli.main(["format", "work", "%icon %5hused"]) == 0
    assert registry.find(registry.load(), "work")["format"] == "%icon %5hused"


def test_format_warns_about_an_unknown_token_but_still_sets_it(capsys, reg_with_work):
    assert cli.main(["format", "work", "%name %bogus"]) == 0
    assert "%bogus" in capsys.readouterr().out
    assert registry.find(registry.load(), "work")["format"] == "%name %bogus"


def test_format_sets_one_tokens_colour(reg_with_work):
    assert cli.main(["format", "work", "--color", "%name", "dim"]) == 0
    assert registry.find(registry.load(), "work")["format_colors"] == {"%name": "dim"}


def test_setting_a_colour_to_auto_deletes_the_key(reg_with_work):
    """Absent means auto, so the dict only ever holds deviations — an untouched
    account carries no colour state at all."""
    cli.main(["format", "work", "--color", "%name", "dim"])
    cli.main(["format", "work", "--color", "%name", "auto"])
    assert registry.find(registry.load(), "work")["format_colors"] == {}


def test_format_rejects_a_bad_colour(reg_with_work):
    assert cli.main(["format", "work", "--color", "%name", "red"]) == 1


def test_format_tokens_lists_them(capsys):
    assert cli.main(["format", "--tokens"]) == 0
    out = capsys.readouterr().out
    for token in ("%icon", "%name", "%5hreset", "%7dquotaleft", "%5h"):
        assert token in out


def test_the_passthrough_still_wins_over_the_new_command(monkeypatch):
    """`ccs -p "format the disk"` is claude's. The two passthrough branches must
    stay ahead of every command name, including this one."""
    seen = []
    monkeypatch.setattr(cli, "cmd_tty", lambda args, gui: seen.append(args) or 0)
    cli.main(["-p", "format"])
    assert seen == [["-p", "format"]]
```

In `tests/test_doctor.py`:

```python
def test_doctor_reports_an_unknown_token(reg_with_work):
    reg = registry.load()
    registry.set_field(reg, "work", "format", "%name %bogus")
    registry.save(reg)
    checks = doctor.run(registry.load())
    bad = [c for c in checks if not c.ok and "%bogus" in (c.detail or "")]
    assert bad, "an unknown token must be reported"
    assert "ccs format" in bad[0].detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_cli.py tests/test_doctor.py -x`
Expected: FAIL — `cli.main(["format", ...])` falls through to the account-slug
lookup and returns nonzero.

- [ ] **Step 3: Write minimal implementation**

In `ccas/cli.py`, add `from . import format as fmt` to the imports, and:

```python
def cmd_format(rest) -> int:
    """`ccs format` — the terminal door to the custom mode's format string.

    The panel sets per-token colour but never the format itself: it is built
    fresh per click and its model is one click, one Action, then close. Free
    text belongs where there is a prompt, which is here.
    """
    if rest and rest[0] == "--tokens":
        for token in fmt.TOKENS:
            print(token)
        return 0
    if not rest:
        return 1
    slug, rest = rest[0], rest[1:]
    reg = registry.load()
    account = registry.find(reg, slug)
    if account is None:
        return 1

    if not rest:
        print(account.get("format") or fmt.DEFAULT_FORMAT)
        for token, color in sorted((account.get("format_colors") or {}).items()):
            print(f"  {token}  {color}")
        return 0

    if rest[0] == "--color":
        if len(rest) < 3 or not fmt.valid_color(rest[2]):
            return 1
        colors = dict(account.get("format_colors") or {})
        if rest[2] == fmt.AUTO:
            colors.pop(rest[1], None)
        else:
            colors[rest[1]] = rest[2]
        return _mutate(slug, "format_colors", colors)

    unknown = fmt.unknown_tokens(rest[0])
    for token in unknown:
        print(f"warning: unknown token {token}")
    return _mutate(slug, "format", rest[0])
```

In `main()`, beside the other field commands (`nick`, `color`, `display`) — which
places it correctly behind the `--` and leading-`-` passthrough branches:

```python
    if command == "format":
        return cmd_format(rest)
```

In `ccas/doctor.py`, inside `_account_checks(account)`:

```python
    unknown = format.unknown_tokens(account.get("format") or "")
    checks.append(Check(
        not unknown, f"{account['slug']} format tokens are known",
        "" if not unknown else
        f"unknown: {' '.join(unknown)} — they render literally on the bar; "
        f"fix with: ccs format {account['slug']} '…'"))
```

with `from . import format` at the top. Doctor reports and does not repair —
rewriting the format here would mask the fault it is looking for.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS, whole suite.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/cli.py ccas/doctor.py tests/test_cli.py tests/test_doctor.py
git commit -m "Add ccs format, and have doctor name an unknown token"
```

---

### Task 7: The panel's state and its one new action

**Files:**
- Modify: `ccas/panel.py:137-149` (`build_state`)
- Modify: `ccas/cli.py:146-181` (`dispatch_panel`)
- Modify: `tests/test_panel.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `state["format"]`, `state["format_colors"]`, `state["format_tokens"]`; the `format_color` action, whose value is a `(token, colour)` tuple

- [ ] **Step 1: Write the failing test**

In `tests/test_panel.py`:

```python
def test_build_state_carries_the_format_and_its_tokens():
    """format_tokens is precomputed here so panel_ui never parses a format
    string — the widget tree renders what it is handed and decides nothing."""
    # set work's format to "%icon %name %icon %5h" first, via the file fixture
    state = panel.build_state("work")
    assert state["format"] == "%icon %name %icon %5h"
    assert state["format_tokens"] == ["%icon", "%name", "%5h"]
    assert state["format_colors"] == {}


def test_build_state_defaults_the_format_for_an_old_account():
    state = panel.build_state("work")     # an account with neither key
    assert state["format"] == fmt.DEFAULT_FORMAT
```

In `tests/test_cli.py`:

```python
def test_the_panel_sets_one_tokens_colour(reg_with_work):
    assert cli.dispatch_panel(panel.Action("format_color", "work", ("%name", "#89b4fa"))) == 0
    assert registry.find(registry.load(), "work")["format_colors"] == {"%name": "#89b4fa"}


def test_the_panel_clears_a_colour_by_choosing_auto(reg_with_work):
    cli.dispatch_panel(panel.Action("format_color", "work", ("%name", "dim")))
    cli.dispatch_panel(panel.Action("format_color", "work", ("%name", "auto")))
    assert registry.find(registry.load(), "work")["format_colors"] == {}


def test_the_panel_cannot_set_the_format_itself(reg_with_work):
    """Format… spawns a terminal instead — the panel is gone by the time it
    starts, and a bar click has no stdin to prompt on."""
    with pytest.raises(ValueError):
        cli.dispatch_panel(panel.Action("format", "work", "%name"))


def test_the_format_button_opens_a_terminal(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_in_terminal", lambda args: seen.append(args) or 0)
    cli.dispatch_panel(panel.Action("edit_format", "work", None))
    assert seen == [["format", "work"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_panel.py tests/test_cli.py -x`
Expected: FAIL — `KeyError: 'format'` from `build_state`.

- [ ] **Step 3: Write minimal implementation**

In `ccas/panel.py`, add `from . import format as fmt` and, in the dict
`build_state` returns, beside `"display"`:

```python
        "format": account.get("format") or fmt.DEFAULT_FORMAT,
        "format_colors": dict(account.get("format_colors") or {}),
        # Precomputed so panel_ui parses nothing: the widget tree renders the
        # list it is handed. Ordered and de-duplicated, so the dropdown reads
        # left to right the way the label does.
        "format_tokens": fmt.tokens_in(account.get("format") or fmt.DEFAULT_FORMAT),
```

In `ccas/cli.py`'s `dispatch_panel`, before the `raise ValueError`:

```python
    if kind == "format_color":
        token, color = value
        if not fmt.valid_color(color):
            return 1
        account = registry.find(registry.load(), slug)
        if account is None:
            return 1
        colors = dict(account.get("format_colors") or {})
        if color == fmt.AUTO:
            colors.pop(token, None)
        else:
            colors[token] = color
        return _mutate(slug, "format_colors", colors)
    if kind == "edit_format":
        return _in_terminal(["format", slug])
```

`format` is deliberately not a branch — the last test above pins that the panel
cannot set the string itself and falls to the `raise ValueError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS, whole suite.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/panel.py ccas/cli.py tests/test_panel.py tests/test_cli.py
git commit -m "Carry the format into the panel's state, and its one new action"
```

---

### Task 8: The panel's widgets — the missing label, and the colour row

**Files:**
- Modify: `ccas/panel_ui.py:686-720` (`_build_toggles`, `_on_mode`)
- Modify: `assets/menu.css`
- Modify: `tests/test_panel.py` (the one new decision, `shows_color_row`)

`panel_ui.py` itself stays untested by the suite, as it is today: it needs GTK,
which the tests must not. Everything it could get wrong that is a *decision* is
pushed into `panel.py` and tested there; the widget tree is checked live, in
Step 4.

**Interfaces:**
- Consumes: `state["display"]`, `state["format"]`, `state["format_colors"]`, `state["format_tokens"]`; `panel.Action("format_color", slug, (token, colour))`, `panel.Action("edit_format", slug, None)`

- [ ] **Step 1: Write the failing test**

`panel_ui` needs GTK and the suite must not, so the tests here are the two that
can be written without it — put them in `tests/test_panel.py`:

```python
def test_the_colour_row_is_only_offered_for_the_custom_mode():
    """panel_ui asks state, not the registry. The rule lives here so it is
    testable without GTK."""
    assert panel.shows_color_row({"display": "custom"}) is True
    for mode in ("nickname", "index", "claude code", "icon only"):
        assert panel.shows_color_row({"display": mode}) is False
```

Add to `ccas/panel.py`:

```python
def shows_color_row(state) -> bool:
    """Whether the panel offers per-token colour. A decision, so it lives here
    and not in panel_ui, which decides nothing."""
    return state.get("display") == "custom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_panel.py -x`
Expected: FAIL — `AttributeError: module 'ccas.panel' has no attribute 'shows_color_row'`.

- [ ] **Step 3: Write minimal implementation**

Add `shows_color_row` to `panel.py` as above. Then in `panel_ui.py`'s
`_build_toggles`, replace the `bottom` row's construction:

```python
    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    bottom.append(Gtk.Label(label="Display as", xalign=0))
    modes = Gtk.DropDown.new_from_strings(paths.DISPLAY_MODES)
    if state["display"] in paths.DISPLAY_MODES:
        modes.set_selected(paths.DISPLAY_MODES.index(state["display"]))
    modes.connect("notify::selected", lambda d, _p: _on_mode(d, slug, state, pick))
    bottom.append(modes)

    edit = Gtk.Button(label="Format…")
    edit.connect("clicked", lambda _b: pick(panel.Action("edit_format", slug, None)))
    edit.set_visible(panel.shows_color_row(state))
    bottom.append(edit)

    # the swatch row, unchanged from here down
```

and append the second row after it:

```python
    if panel.shows_color_row(state):
        box.append(_build_token_colors(state, pick))
```

with:

```python
def _build_token_colors(state, pick):
    """Which token, and what colour. The tokens come precomputed in
    state["format_tokens"] — this parses nothing."""
    from gi.repository import Gtk

    slug = state["slug"]
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.add_css_class("ccas-token-colors")
    row.append(Gtk.Label(label="Colour of", xalign=0))

    tokens = state["format_tokens"] or ["%name"]
    picker = Gtk.DropDown.new_from_strings(tokens)
    row.append(picker)

    def chosen():
        return tokens[picker.get_selected()]

    for name, value in (("auto", "auto"), ("dim", "dim")):
        chip = Gtk.Button(label=name)
        chip.add_css_class("ccas-chip")
        chip.connect("clicked", lambda _b, v=value:
                     pick(panel.Action("format_color", slug, (chosen(), v))))
        row.append(chip)

    for _name, hexcolor in paths.PALETTE:
        swatch = Gtk.Button()
        swatch.add_css_class("ccas-swatch")
        swatch.add_css_class(_tint(hexcolor))
        swatch.connect("clicked", lambda _b, v=hexcolor:
                       pick(panel.Action("format_color", slug, (chosen(), v))))
        row.append(swatch)

    entry = Gtk.Entry(placeholder_text="#rrggbb", max_length=7, width_chars=8)
    # activate only: a colour is a small enough commitment for Enter-then-close,
    # where a rename is not — that one still goes to a terminal.
    entry.connect("activate", lambda e:
                  pick(panel.Action("format_color", slug, (chosen(), e.get_text()))))
    row.append(entry)
    return row
```

In `assets/menu.css`, add a rule for `.ccas-token-colors` matching the existing
`.ccas-toggles` spacing, and `.ccas-chip` sized like `.ccas-swatch` but with
visible text. **`install.sh` copies `menu.css` once and never overwrites it**, so
the live install will not pick this up — say so when reporting, and the user
copies it across if they want it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS, whole suite.

Then the live check, which is the only way to see this row at all:

```bash
cd ~/ccas && ./install.sh          # ccs runs the installed copy, not this repo
ccs display <slug> custom
ccs format <slug> '%icon %name %5hused'
ccs --gui <slug>                   # the panel; a second call closes it
```

Confirm on screen: the dropdown now says **Display as**, the second row appears,
and it lists exactly `%icon`, `%name`, `%5hused`. Screenshot it with `grim`
cropped to the panel rather than asking the user to look — Waybar is on
`HDMI-A-1` (x 2560–4480), `DP-1` is x 0–2560. To click a swatch, use
`swaymsg seat - cursor move` (never `cursor set`) then `cursor press button1` —
the recipe is in CLAUDE.md under Working style.

Check the invariant around the commands, not against a constant:

```bash
before=$(stat -c %Y ~/.claude/.credentials.json)
ccs format <slug> '%icon %name'
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo BUG
find ~/.claude -maxdepth 1 -type l    # must stay empty
ccs doctor
```

- [ ] **Step 5: Commit**

```bash
cd ~/ccas && git add ccas/panel.py ccas/panel_ui.py assets/menu.css tests/test_panel.py
git commit -m "Label the display dropdown, and add the per-token colour row"
```

---

### Task 9: The documentation

**Files:**
- Modify: `CLAUDE.md` (the module table, the Commands block)
- Modify: `docs/waybar-setup.md` (the token table)
- Modify: `docs/why.md` (one section)

- [ ] **Step 1: Write the docs**

`CLAUDE.md` — add to the module table, after `label.py`:

```
| `format.py` | the custom display mode: the token table, the parser, per-token colour. Pure; no GTK. |
```

and to the Commands block:

```bash
ccs format <slug> ['<fmt>']      # the custom mode's format string; --tokens lists them
```

`docs/waybar-setup.md` — the full token table from the spec, plus the note that
the four built-in modes no longer show usage and `%icon %name %5h` is how to get
the old bar back.

`docs/why.md` — a section, because this was a real fault found on the real bar:

> **"Icon only" was an icon and a clock.** `label.render` appended the usage
> token to every display mode, so the mode that named itself icon-only was the
> one mode that could not produce a bare icon — and nothing could ask for the
> percentage instead of the clock, or drop the clock while keeping a nickname.
> The four modes named a *text* choice but returned a text choice plus a usage
> decision nobody made. The fix was not another mode with another fixed shape:
> it was to let the shape be the user's. `%icon %name %5h` is exactly what the
> built-ins used to render, which is what made removing the clock from them safe.

- [ ] **Step 2: Verify**

Run: `cd ~/ccas && python -m pytest && ccs doctor`
Expected: the suite passes and doctor is clean.

- [ ] **Step 3: Commit**

```bash
cd ~/ccas && git add CLAUDE.md docs/waybar-setup.md docs/why.md
git commit -m "Document the custom format and the tokens it takes"
```
