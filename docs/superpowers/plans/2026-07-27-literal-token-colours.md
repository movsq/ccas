# Literal Token Colours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the `account` token colour and the `%icon` token, so every token colour is a literal hex and the ✻ glyph belongs to the widget; then replace the panel's colour-target dropdown with a row of chips carrying a 2 px colour strip.

**Architecture:** `format.py` loses its one remaining colour indirection (`_resolve`) and its one markup-returning token, so `_emit` becomes uniform and `render()` prefixes the glyph itself. `registry.add()` writes one random hex into both the account's `color` and the used-percentage token, which reproduces today's visible result without the indirection. `panel_ui._build_color_editor` swaps a `Gtk.DropDown` for a `Gtk.Box` of `Gtk.Button` chips, each with its own `CssProvider`; the existing settle-timer write path is untouched.

**Tech Stack:** Python 3.14, stdlib only at runtime. GTK4 via PyGObject, imported *inside* `panel_ui.show()` only. Tests are pytest, ~471 of them, run in under a second.

## Global Constraints

- **Read `CLAUDE.md` before starting.** Its "Hard invariants" section governs every task here.
- **stdlib only** at runtime. No new dependencies.
- **Never write to `~/.claude`.** Nothing in this plan should touch it; if a test does, that is the bug.
- **Never delete a file** — move it to `.claude_trash/` in the checkout.
- **Never co-sign or co-author a commit.** No `Co-Authored-By`, no trailers.
- **Nothing migrates.** A stored value that is no longer valid reads as absent and renders `format.DEFAULT_COLOR`. Do not add a read path that understands `auto`, `dim`, or `account`.
- **`gi` is imported inside `show()`**, never at module scope. `tests/test_panel_ui.py` pins this; do not weaken those tests.
- **A per-widget `CssProvider` goes on at `Gtk.STYLE_PROVIDER_PRIORITY_USER + 1`**, never `PRIORITY_APPLICATION` — `menu.css` is installed at `PRIORITY_USER` and outranks it.
- **TDD.** Failing test → verify it fails → implement → verify it passes → commit. One commit per task.
- Run the whole suite with `cd /home/fixed/ccas && python -m pytest -q`.
- **`ccs` runs the installed copy.** Re-run `./install.sh` before any live check.
- Spec: `docs/superpowers/specs/2026-07-27-literal-token-colours-design.md`.

---

## File Structure

| File | Change |
|---|---|
| `ccas/format.py` | Delete `ACCOUNT`, `_resolve`. `valid_color` accepts hex only. `%icon` leaves `TOKENS`; `render()` prefixes `_icon()`. `DEFAULT_FORMAT` drops the glyph; `DEFAULT_FORMAT_COLORS` becomes `default_format_colors(color)`. |
| `ccas/label.py` | `check_invisible_warning` stops special-casing the string `%icon`. |
| `ccas/registry.py` | `add()` draws one hex and writes it to `color` and `%5hused`. `set_field`'s comment updated. |
| `ccas/cli.py` | `cmd_format --color` rejects `account`; help text updated. |
| `ccas/panel.py` | `color_targets()` labels the first entry `icon`; unrecognised token colours seed `DEFAULT_COLOR`. |
| `ccas/panel_ui.py` | `_build_color_editor` replaces the dropdown with a chip row. |
| `assets/menu.css` | Chip styling replaces the dropdown rules. |
| `tests/test_format.py` | Rewrite the `account`/`%icon` tests. |
| `tests/test_label.py`, `tests/test_registry.py`, `tests/test_cli.py`, `tests/test_panel.py`, `tests/test_doctor.py`, `tests/test_pickers.py` | Update fixtures and assertions that name `%icon` or `account`. |
| `docs/why.md`, `CLAUDE.md`, `docs/waybar-setup.md` | Record the bug and the new rules. |

---

### Task 1: `account` stops being a colour

**Files:**
- Modify: `ccas/format.py:21`, `ccas/format.py:41-46`, `ccas/format.py:85-104`, `ccas/format.py:219-226`
- Test: `tests/test_format.py`

**Interfaces:**
- Produces: `format.valid_color(value) -> bool` accepting only `#rrggbb`. `format._resolve` no longer exists. `format.ACCOUNT` no longer exists.

- [ ] **Step 1: Write the failing tests**

In `tests/test_format.py`, replace the two tests that assert `fmt.valid_color("account")` (currently at lines 189 and 257) and the one at line 272-273 that renders a stored `"account"`, with:

```python
def test_account_is_no_longer_a_colour():
    """The one indirection left after auto/dim went. A colour setting that
    might or might not reach the label is the same defect auto had: the user
    dragged `the widget` for ten minutes against an account whose tokens all
    held literal hexes, and nothing on screen ever moved."""
    assert not fmt.valid_color("account")
    assert not hasattr(fmt, "ACCOUNT")


def test_a_stored_account_renders_as_the_default_colour():
    """Nothing migrates: an unrecognised value reads as absent, exactly as a
    retired `auto` or `dim` does."""
    a = account(color="#f38ba8", format="%name", nickname="n",
                format_colors={"%name": "account"})
    assert f"color='{fmt.DEFAULT_COLOR}'" in fmt.render(a, 1)
    assert "#f38ba8" not in fmt.render(a, 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_format.py -q -k "account_is_no_longer or stored_account_renders"`
Expected: FAIL — `valid_color("account")` currently returns True and `fmt.ACCOUNT` exists.

- [ ] **Step 3: Implement**

Delete line 21 (`ACCOUNT = "account"`). Rewrite `valid_color`:

```python
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
```

Delete `_resolve` (lines 92-94). In `_emit`, line 225 becomes:

```python
    attrs = f"color='{_chosen(ctx['account'], token)}'"
```

In `_icon`, line 104 becomes — temporarily, until Task 2 rewrites this function:

```python
        attrs = f"color='{_chosen(ctx['account'], '%icon')}'"
```

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest -q`
Expected: the two new tests PASS. Other failures are expected here — `tests/test_registry.py:250` asserts `"account"` is rejected by `set_field` (that one should now pass), while `tests/test_format.py:280` still asserts `DEFAULT_FORMAT_COLORS` contains `"account"` and `tests/test_cli.py` still exercises `--color … account`. **Leave those failing; Tasks 2, 4 and 5 fix them.** Do not paper over them.

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/format.py tests/test_format.py
git commit -m "format: a token's colour is a hex or nothing — account is gone"
```

---

### Task 2: the glyph stops being a token

**Files:**
- Modify: `ccas/format.py:34-38`, `ccas/format.py:96-107`, `ccas/format.py:165-172`, `ccas/format.py:219-248`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: `format.valid_color`, `format._chosen` from Task 1.
- Produces: `format.DEFAULT_FORMAT == "%email %5hused"`. `format.default_format_colors(color: str) -> dict` returning `{"%email": GREY, "%5hused": color}`. `format.DEFAULT_FORMAT_COLORS` no longer exists. `"%icon" not in format.TOKENS`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_format.py:34` and `:40` (the two `format_override="%icon"` tests), `:65`, `:70`, `:175-184`, `:203`, and `:278-280` with these. Keep the existing `account()` fixture.

```python
def test_the_glyph_is_drawn_ahead_of_the_format_string():
    """It was never layout. `%icon` and the panel's `the widget` were two names
    for one colour, which is what made the colour editor unreadable — you could
    set them to different values and only one of them was the bar."""
    a = account(color="#89b4fa", format="%name", nickname="n")
    out = fmt.render(a, 1)
    assert out.startswith(f"<span size='{fmt.ICON_SIZE}' "
                          f"rise='{fmt.ICON_RISE}' color='#89b4fa'>✻</span>")
    assert out.endswith("n</span>")


def test_the_glyph_follows_the_account_colour_with_no_token_to_override_it():
    a = account(color="#a6e3a1", format="%name", nickname="n",
                format_colors={"%icon": "#f38ba8"})
    assert "#a6e3a1" in fmt.render(a, 1)
    assert "#f38ba8" not in fmt.render(a, 1)


def test_hide_icon_removes_the_glyph_entirely():
    """No alpha='1' spacer: the glyph is no longer named in the format string,
    so an invisible one would reserve width in every label that hid it."""
    a = account(hide_icon=True, format="%name", nickname="n")
    out = fmt.render(a, 1)
    assert "✻" not in out
    assert out == f"<span size='{fmt.TEXT_SIZE}' " \
                  f"color='{fmt.DEFAULT_COLOR}'>n</span>"


def test_icon_is_not_a_token_and_renders_as_its_own_name():
    """A retired token renders literally rather than raising — visible and
    self-explaining, which is what tells the user to re-set their format."""
    assert "%icon" not in fmt.TOKENS
    assert fmt.unknown_tokens("%icon %name") == ["%icon"]
    assert fmt.tokens_in("%icon %name") == ["%name"]
    a = account(format="%icon", hide_icon=True)
    assert fmt.render(a, 1) == "%icon"


def test_the_default_format_and_its_colours():
    """The preset a new account gets. The glyph is not in it because the glyph
    is not a token; its colour is the account's."""
    assert fmt.DEFAULT_FORMAT == "%email %5hused"
    assert fmt.default_format_colors("#89b4fa") == {
        "%email": fmt.GREY, "%5hused": "#89b4fa"}
    assert not hasattr(fmt, "DEFAULT_FORMAT_COLORS")
```

Also update the two survivors that merely mention the token:
- line 65 → `assert fmt.tokens_in("%name %email %name %5h") == ["%name", "%email", "%5h"]`
- line 70 → `assert fmt.render(account(nickname=None), 1, format_override="%name %email")` — adjust the expected string to drop the glyph span, since `format_override` still goes through the glyph prefix.
- line 203 → change `format="%icon %name %5h"` to `format="%name %5h"` and drop the glyph from the expectation.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_format.py -q`
Expected: the five new tests FAIL — `default_format_colors` does not exist, `%icon` is still in `TOKENS`, `render()` does not prefix the glyph.

- [ ] **Step 3: Implement**

Replace lines 34-38 of `ccas/format.py`:

```python
# The glyph is not in it: it is the widget's own mark, drawn by render()
# ahead of whatever the format asks for, and hidden only by hide_icon.
DEFAULT_FORMAT = "%email %5hused"


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
```

Rewrite `_icon` (lines 96-107):

```python
def _icon(ctx):
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
```

Delete `"%icon": None,` from `TOKENS` (line 172) and update the comment above it (lines 165-170):

```python
# Every entry is a text function. The glyph is not here: it returns finished
# markup and is drawn by render() rather than placed by the format string.
```

Delete the `%icon` special case in `_emit` (lines 220-221).

In `render()`, prefix the glyph. Replace the final line (`return _collapse(pieces)`) with:

```python
    body = _collapse(pieces)
    glyph = _icon(ctx)
    if not glyph:
        return body
    return f"{glyph} {body}" if body else glyph
```

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_format.py -q`
Expected: PASS. `tests/test_label.py`, `tests/test_registry.py` and `tests/test_cli.py` still fail — Tasks 3, 4 and 5.

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/format.py tests/test_format.py
git commit -m "format: the glyph belongs to the widget, not the format string"
```

---

### Task 3: the invisible-label warning

**Files:**
- Modify: `ccas/label.py:50-67`
- Test: `tests/test_label.py`

**Interfaces:**
- Consumes: `format.DEFAULT_FORMAT` from Task 2.

- [ ] **Step 1: Write the failing test**

Update `tests/test_label.py:26-27` and `:38` (which use `format="%icon %name"`), then add:

```python
def test_a_hidden_glyph_and_an_empty_format_is_the_invisible_case():
    """The glyph is no longer a token, so the test is the format string being
    empty rather than being nothing but %icon."""
    a = account(hide_icon=True, format="   ")
    assert label.check_invisible_warning(a) is True
    assert label.render(a, 1) == ""


def test_a_hidden_glyph_with_text_left_is_not_invisible():
    a = account(hide_icon=True, format="%name", nickname="n")
    assert label.check_invisible_warning(a) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_label.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `ccas/label.py`, replace the docstring paragraph and the `invisible =` line:

```python
    """True when the caller should notify. Latches, and re-arms on exit.

    "Invisible" used to be the hidden glyph plus the "icon only" mode, then the
    hidden glyph plus a format string holding nothing but `%icon`. With the
    glyph no longer a token the test is simpler still: hide it and leave the
    format empty and there is nothing on the bar at all.
    """
    from . import format as fmt
    fmt_string = account.get("format") or fmt.DEFAULT_FORMAT
    invisible = account["hide_icon"] and not fmt_string.strip()
```

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_label.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/label.py tests/test_label.py
git commit -m "label: invisible is a hidden glyph and an empty format"
```

---

### Task 4: a new account's colours are literal

**Files:**
- Modify: `ccas/registry.py:73-88`, `ccas/registry.py:126-133`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `format.default_format_colors`, `format.random_color` from Task 2.

- [ ] **Step 1: Write the failing test**

Replace the existing `test_add_gives_each_account_its_own_colour` body with, and add:

```python
def test_a_new_accounts_colours_are_all_literal_hexes():
    """The preset the user asked for — glyph and used-percentage in one random
    colour, the address grey — with no indirection to reach it. The glyph
    follows `color` by definition, so only %5hused needs the hex written in."""
    reg = registry.blank()
    a = registry.add(reg, "work", "w@e.com", None)
    assert fmt.HEX.match(a["color"])
    assert a["format"] == fmt.DEFAULT_FORMAT
    assert a["format_colors"] == {"%email": fmt.GREY, "%5hused": a["color"]}
    assert all(fmt.HEX.match(v) for v in a["format_colors"].values())


def test_two_new_accounts_do_not_share_a_colour():
    reg = registry.blank()
    first = registry.add(reg, "a", "a@e.com", None)
    second = registry.add(reg, "b", "b@e.com", None)
    assert first["color"] != second["color"]
```

(`registry.blank()` — check the real constructor name in `ccas/registry.py` and use it; the fixture in the existing test file already builds one.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_registry.py -q`
Expected: FAIL — `format_colors` still holds `"account"` for `%icon` and `%5hused`.

- [ ] **Step 3: Implement**

In `registry.add()`, replace the `"color"` / `"format_colors"` lines:

```python
    color = format.random_color()
    account = {
        "slug": slug,
        "nickname": nickname,
        "email": email,
        # Random, and written into the used-percentage as a literal so the
        # glyph and the number match from the first paint. They no longer
        # follow one another — moving one is one drag, moving both is two,
        # and in exchange the format string says what it will look like.
        "color": color,
        "hide_icon": False,
        "format": format.DEFAULT_FORMAT,
        "format_colors": format.default_format_colors(color),
        ...
```

Update the comment in `set_field` (line ~129) — it names `auto/dim/account`:

```python
        # HEX and nothing else. There is no longer any other shape a colour can
        # take, here or in format_colors.
```

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/registry.py tests/test_registry.py
git commit -m "registry: a new account's token colours are literal hexes"
```

---

### Task 5: the CLI rejects `account`

**Files:**
- Modify: `ccas/cli.py:175-186`, and the `--color` line in the usage/help text (grep for `--color` in `ccas/cli.py`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `format.valid_color` from Task 1.

- [ ] **Step 1: Write the failing test**

Find the existing test that passes `account` to `--color` and replace it with:

```python
def test_format_color_rejects_every_retired_colour_name(capsys, sandbox):
    """auto, dim and account each meant "work it out from something else".
    All three are gone and none of them may come back through the CLI."""
    reg = registry.load()
    registry.add(reg, "work", "w@e.com", None)
    registry.save(reg)
    for retired in ("auto", "dim", "account"):
        assert cli.cmd_format(["work", "--color", "%email", retired]) == 1


def test_format_color_dash_still_clears_a_token(sandbox):
    reg = registry.load()
    registry.add(reg, "work", "w@e.com", None)
    registry.save(reg)
    assert cli.cmd_format(["work", "--color", "%email", "#89b4fa"]) == 0
    assert cli.cmd_format(["work", "--color", "%email", "-"]) == 0
    account = registry.find(registry.load(), "work")
    assert "%email" not in account["format_colors"]
```

(Use whatever sandbox fixture `tests/test_cli.py` already uses — every path must go through the `CCAS_*` overrides.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_cli.py -q -k "retired_colour or dash_still_clears"`
Expected: `account` currently returns 0.

- [ ] **Step 3: Implement**

No logic change is needed — `valid_color` already refuses it after Task 1. Confirm the guard at `ccas/cli.py:179` reads `rest[2] == "-" or fmt.valid_color(rest[2])` and update the surrounding help text and the `ccs format --color` line in `CLAUDE.md`'s command table from `account, #rrggbb, or -` to `#rrggbb, or - to clear`.

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest -q`
Expected: PASS, all of it. If `tests/test_doctor.py:289` or `tests/test_pickers.py:151` still fail, they are fixture strings containing `%icon` — update them to a token that still exists (`%name`).

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/cli.py tests/ CLAUDE.md
git commit -m "cli: account is not a colour a token may be given"
```

---

### Task 6: `color_targets` stops seeding from the account

**Files:**
- Modify: `ccas/panel.py:143-167`
- Test: `tests/test_panel.py`

**Interfaces:**
- Produces: `panel.color_targets(account) -> list[{"token": str|None, "label": str, "color": str}]`, first entry `{"token": None, "label": "icon", "color": account["color"]}`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_first_colour_target_is_the_icon():
    """It paints the glyph on the bar and the dot in the panel's account list.
    It was called `the widget` while it could reach neither — the account had
    no token pointing at it, so the slider moved nothing at all."""
    a = _account(color="#89b4fa", format="%name")
    targets = panel.color_targets(a)
    assert targets[0] == {"token": None, "label": "icon", "color": "#89b4fa"}


def test_an_unrecognised_token_colour_seeds_the_default_not_the_account():
    """Seeding from the account colour was defensible while `account` existed.
    Now it puts a colour in the sliders that has nothing to do with the token,
    and the settle timer then writes it."""
    a = _account(color="#89b4fa", format="%name",
                 format_colors={"%name": "dim"})
    token = next(t for t in panel.color_targets(a) if t["token"] == "%name")
    assert token["color"] == fmt.DEFAULT_COLOR
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/fixed/ccas && python -m pytest tests/test_panel.py -q -k "colour_target or unrecognised_token"`
Expected: FAIL — the label is `the widget` and the seed is the account colour.

- [ ] **Step 3: Implement**

```python
def color_targets(account: dict) -> list:
    """What the panel's chip row offers: the icon, then each token in this
    account's format string.

    A decision, so it lives here — panel_ui renders the list it is handed.

    The icon leads and is always present: it is the account's own colour,
    painting the ✻ on the bar and the dot in the panel's account list. It was
    called `the widget` back when a token could point at it, which is when it
    could also silently point at nothing.

    `color` is what the sliders should open at. A token holding nothing — or a
    retired `auto`, `dim` or `account` — opens at DEFAULT_COLOR, which is what
    it renders as. Seeding it from the account's colour would show the user a
    value the label has never used, and the settle timer would then write it.
    """
    own = account.get("color") or paths.PALETTE[0][1]
    colors = account.get("format_colors") or {}
    targets = [{"token": None, "label": "icon", "color": own}]
    for token in fmt.tokens_in(account.get("format") or fmt.DEFAULT_FORMAT):
        value = colors.get(token) or ""
        targets.append({
            "token": token,
            "label": token,
            "color": value if fmt.HEX.match(value) else fmt.DEFAULT_COLOR,
        })
    return targets
```

- [ ] **Step 4: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/fixed/ccas
git add ccas/panel.py tests/test_panel.py
git commit -m "panel: the first colour target is the icon, and seeds honestly"
```

---

### Task 7: the chip row

**Files:**
- Modify: `ccas/panel_ui.py:886-1036`
- Modify: `assets/menu.css:106-140`
- Test: `tests/test_panel_ui.py` (boundary tests only — the widget tree is not unit-tested here; it is verified live in Task 8)

**Interfaces:**
- Consumes: `panel.color_targets` from Task 6, unchanged `panel.Action`, `panel.NO_REBUILD`, `SETTLE_MS`.

- [ ] **Step 1: Replace the dropdown with chips**

In `_build_color_editor`, delete the `picker = Gtk.DropDown.new_from_strings(...)` line and its `top.append(picker)`. The `Gtk.Label(label="Color of", xalign=0)` moves onto its own row above the chips. Then:

```python
    # A row of chips, not a dropdown. The dropdown showed one target at a time,
    # cost a click and a popup to change, and — greyed by an over-broad CSS
    # rule — read as a disabled control. The chips show every target's current
    # colour at once, which is the thing the user could not see.
    chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    chips.add_css_class("ccas-chips")

    # The name stays at full contrast and the colour goes in a 2 px strip under
    # it: a name painted in its own colour is unreadable at low saturation and
    # indistinguishable from "unset" at white, which is exactly the colour an
    # unset token renders as.
    strips = []
    buttons = []
    for index, entry_target in enumerate(targets):
        chip = Gtk.Button()
        chip.add_css_class("ccas-chip")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.append(Gtk.Label(label=entry_target["label"]))
        strip = Gtk.Box()
        strip.add_css_class("ccas-chip-strip")
        # Its own provider, at PRIORITY_USER + 1, for the reason the preview
        # swatch has one: menu.css is installed at PRIORITY_USER, so an
        # application-priority rule loses to it silently, and a colour part-way
        # through a drag belongs to none of the classes _load_tints() built.
        strip_provider = Gtk.CssProvider()
        strip.get_style_context().add_provider(
            strip_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        strip_provider.load_from_string(
            f".ccas-chip-strip {{ background: {entry_target['color']}; }}")
        inner.append(strip)
        chip.set_child(inner)
        chips.append(chip)
        strips.append(strip_provider)
        buttons.append(chip)
    box.append(Gtk.Label(label="Color of", xalign=0))
    box.append(chips)
```

- [ ] **Step 2: Rewire target selection**

`target()` currently reads `picker.get_selected()`. Replace it, and add the selection paint:

```python
    def target():
        return targets[min(ui_state["target"], len(targets) - 1)]

    def show_selection():
        for index, chip in enumerate(buttons):
            if index == ui_state["target"]:
                chip.add_css_class("ccas-chip-on")
            else:
                chip.remove_css_class("ccas-chip-on")

    def choose(index):
        # Selection is a background, never a colour: the strip means "this is
        # the colour" and the background means "this is what you are editing".
        # One cue doing both jobs is unreadable.
        ui_state["target"] = index
        show_selection()
        seed()

    for index, chip in enumerate(buttons):
        chip.connect("clicked", lambda _b, i=index: choose(i))

    ui_state["target"] = min(ui_state.get("target", 0), len(targets) - 1)
    show_selection()
```

Delete the `picker.set_selected(...)` line, the `on_target` function and the `picker.connect("notify::selected", on_target)` line — their comments about `notify::selected` firing on build no longer apply, but the reason they existed does: `ui_state["target"]` is still restored from outside the rebuilt subtree, because `refresh_toggles()` destroys this subtree on every applied setting.

- [ ] **Step 3: Track the drag on the selected chip's strip**

In `paint(value)`, after the preview provider line, add:

```python
        strips[ui_state["target"]].load_from_string(
            f".ccas-chip-strip {{ background: {value}; }}")
```

- [ ] **Step 4: Style the chips**

In `assets/menu.css`, replace the `.ccas-color-editor dropdown …` rules (lines 111-120 and 136-140) with:

```css
/* The chips that pick which element you are colouring. The name stays at full
 * contrast; the colour lives in the strip under it. */
.ccas-chip {
  min-height: 22px; padding: 4px 10px 3px 10px; border-radius: 8px;
  border: none; background: rgba(255, 255, 255, 0.06);
}
.ccas-chip:hover { background: rgba(255, 255, 255, 0.12); }
.ccas-chip.ccas-chip-on { background: rgba(255, 255, 255, 0.20); }
.ccas-chip label { color: #cdd6f4; font-size: 0.85em; }
/* Inset, so it reads as belonging to the chip rather than edging it. */
.ccas-chip-strip { min-height: 2px; margin: 0 2px; border-radius: 999px; }
.ccas-toggles dropdown > button,
.ccas-color-editor entry {
  min-height: 22px; padding: 2px 10px; border-radius: 8px;
  border: none; background: rgba(255, 255, 255, 0.06);
}
.ccas-toggles dropdown > button:hover { background: rgba(255, 255, 255, 0.12); }
.ccas-color-editor entry { font-family: monospace; font-size: 0.85em; }
.ccas-color-editor entry:focus-within { background: rgba(255, 255, 255, 0.10); }
```

and change the slider-name rule so it no longer needs the dropdown exception:

```css
/* The captions only ("Color of", the two slider names). The chips carry their
 * own colour rule above, so there is no dropdown label to exempt any more. */
.ccas-color-editor > label,
.ccas-color-editor > box > label { color: #9399b2; }
```

- [ ] **Step 5: Run the suite**

Run: `cd /home/fixed/ccas && python -m pytest -q`
Expected: PASS. In particular `tests/test_panel_ui.py`'s two boundary tests must still pass — nothing new may be imported at module scope.

- [ ] **Step 6: Commit**

```bash
cd /home/fixed/ccas
git add ccas/panel_ui.py assets/menu.css
git commit -m "Panel: a row of colour chips replaces the target dropdown"
```

---

### Task 8: verify it on the real system

**Files:** none — this task changes the live install and the user's registry, and produces evidence.

Read the **Working style** section of `CLAUDE.md` before starting. Three of its rules decide this task: `ccs` runs the installed copy, `assets/menu.css` does not reach the live panel by itself, and `pkill -f` matches the shell you type it in.

- [ ] **Step 1: Install, and copy the stylesheet across by hand**

```bash
cd /home/fixed/ccas && ./install.sh
cp ~/.config/ccas/menu.css /home/fixed/ccas/.claude_trash/menu.css.live-$(date +%Y%m%d-%H%M%S)
cp assets/menu.css ~/.config/ccas/menu.css
```

The backup is not optional — `install.sh` copies that file once and never again, so the live one may hold the user's own edits.

- [ ] **Step 2: Re-set both accounts' format strings**

They still contain `%icon`, which is now an unknown token and would print as literal text on the bar. This is an explicit step, not a migration:

```bash
ccs format vsed '%5hreset %5hquotaleft'
ccs format vo-sedlacek '%5hreset %5hquotaleft'
ccs render vsed && echo && ccs render vo-sedlacek
```

Expected: each label starts with a `<span size='150%' rise='-800' color='#…'>✻</span>` whose colour equals that account's `color` in `~/.cc-accounts/accounts.json`, and contains no literal `%icon`.

- [ ] **Step 3: Confirm the invariant held**

```bash
find ~/.claude -maxdepth 1 -type l    # must print nothing
```

Check `~/.claude/.credentials.json`'s mtime before and after step 1 — and read `CLAUDE.md`'s note on this before calling a change a bug, because Claude Code running under the default account refreshes that file on its own.

- [ ] **Step 4: Prove a colour change reaches the bar**

```bash
grim -g "3420,0 220x22" /tmp/before.png
ccs format vsed --color %5hquotaleft '#ff0000'
sleep 1.2
grim -g "3420,0 220x22" /tmp/after.png
```

Compare with PIL and count strongly-red pixels in `after.png`. Expected: non-zero. Then restore the colour the account had before you started. **Crop to the widgets — never diff the whole bar**, because the clock and stopwatch tick on their own and report CHANGED every time.

- [ ] **Step 5: Open the panel and look at the chips**

```bash
(CCAS_PANEL_OUTPUT=HDMI-A-1 setsid ccs --gui vsed > /tmp/panel.log 2>&1 &)
sleep 3
grim -g "2560,0 1920x1080" /tmp/panel.png
```

`CCAS_PANEL_OUTPUT` is required — it skips the pointer probe, which never answers on an idle output. Check on the screenshot: three chips, names legible in light text, a coloured strip under each, and the selected one on a lighter background. To close it, kill the pid — do **not** use `pkill -f`:

```bash
for p in $(pgrep -x python3); do
  tr '\0' ' ' < /proc/$p/cmdline | grep -q 'bin/ccs' && kill $p
done
```

- [ ] **Step 6: `ccs doctor`**

Run: `ccs doctor`
Expected: rc 0, everything green.

- [ ] **Step 7: Commit nothing, report everything**

There is nothing to commit here. Report to the user: the two format strings you re-set, the screenshot, the red-pixel count, and `ccs doctor`'s output.

---

### Task 9: the docs

**Files:**
- Modify: `docs/why.md`, `CLAUDE.md`, `docs/waybar-setup.md`

- [ ] **Step 1: `docs/why.md`**

Add one section. It records a bug found on the real system, which is what that file is for. Cover: the user dragged the panel's `the widget` slider for ten minutes against an account all of whose tokens held literal hexes; the slider, the hex field and the preview swatch were all live and correct, and the account colour reached nothing in the label; the control could not tell you whether it would be seen. Note that `account` had already been half-retired — its chip was removed from the panel earlier the same day while it stayed legal in the registry and stayed the default for every new account — and that a UI which stops offering something the data model keeps using is the worst of both. No test counts, no dates-as-status.

- [ ] **Step 2: `CLAUDE.md`**

Replace the invariant headed **A token's colour is `account`, a hex, or nothing** with:

```markdown
**A token's colour is a hex, or nothing.** Nothing means `format.DEFAULT_COLOR`,
white, and it means that for every token. `auto` made the answer depend on which
token asked — the usage ramp for the windowed ones, the account colour for the
glyph. `account` made it depend on whether *any* token asked: an account whose
tokens all held literal hexes had a live, previewed, entirely inert widget-colour
control, and the user spent ten minutes finding that out. There is no usage ramp
in the label; `usage.color()` still ramps inside `usage.bar()` and the panel's
bars, which is where pressure is shown.

**The glyph is the widget's, not the format's.** `%icon` is not a token. The ✻ is
drawn by `format.render()` ahead of the format string, in the account's colour,
and `hide_icon` removes it outright — no `alpha='1'` spacer, which would reserve
the glyph's width in exactly the labels that asked not to have one. One colour
with one name: it paints the bar's glyph and the panel's account dot, and nothing
can disagree with it.
```

Update the command table's `ccs format --color` line to drop `account`.

- [ ] **Step 3: `docs/waybar-setup.md`**

Update the default format (`%email %5hused`) and anything that names `%icon` as a token.

- [ ] **Step 4: Commit**

```bash
cd /home/fixed/ccas
git add docs/why.md CLAUDE.md docs/waybar-setup.md
git commit -m "Docs: what the account colour cost, and the glyph's new home"
```

---

## Self-Review

**Spec coverage.** `ACCOUNT` deleted → Task 1. `%icon` deleted, `_icon` recoloured, `hide_icon` → Task 2 (plus Task 3 for the warning it feeds). Account colour becomes one thing → Tasks 2 and 6. Chip row, strips, providers, selection → Task 7. `color_targets` shape and seeding → Task 6. `DEFAULT_FORMAT` / `default_format_colors` / `registry.add` → Tasks 2 and 4. `--color account` rejected, `-` still clears → Task 5. Nothing migrates, and the explicit re-set of the two stored format strings → Task 8 Step 2. Tests → each task's own steps. Non-goals need no task.

**One thing the spec left implicit** and this plan settles: `label.check_invisible_warning` reads the format string for `%icon`, so it had to change with the token. Task 3.

**One thing the plan settles beyond the spec:** `hide_icon` now emits nothing rather than an `alpha='1'` span. The spacer existed for a glyph the format had asked for; with the glyph implicit it would penalise every label that hid it. Task 8 Step 5's screenshot is where a hidden-glyph widget would show up as too narrow to click, if that judgement is wrong.
