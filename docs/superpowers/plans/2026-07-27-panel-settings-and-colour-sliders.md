# Panel Settings drawer and colour sliders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking. Execution for *this* plan is inline, by the user's choice — Tasks 7
> and 8 need a live panel on screen and a pointer driven at it, which is not
> work to hand to a subagent.

**Goal:** Make `custom` the only display mode, replace the panel's two swatch
rows with a single hue/saturation slider editor, and move account and widget
settings behind a labelled Settings drawer.

**Architecture:** Colour storage moves from an index into `paths.PALETTE` to a
hex string, so the account's colour and a format token's colour are the same
shape. A new `account` colour value lets a token follow the widget. The HSL
arithmetic lives in `format.py` with the other pure functions, so it is testable
without GTK; `panel_ui.py` keeps deciding nothing.

**Tech Stack:** Python 3.14 stdlib only (`colorsys` for the HSL maths), GTK4 via
PyGObject inside `panel_ui.show()` only, pytest.

## Global Constraints

- **Stdlib only at runtime.** `colorsys` is stdlib; nothing new is added.
- **Never write to `~/.claude`.** Nothing in this plan goes near it.
- **`gi` is imported inside functions in `panel_ui.py`, never at module scope.**
  `ccs statusline` runs in every prompt of every session and must not need
  PyGObject. Two existing tests pin this.
- **`format.py` stays pure** — no I/O, no GTK, no registry import.
- **`panel.py` decides, `panel_ui.py` renders.** Any new list or default goes in
  `panel.build_state`, not in the widget tree.
- **Write only on a change; `waybar.signal()` rides on the write.** Slider drags
  must not write per motion event.
- **Never delete files** — move to `.claude_trash` (global CLAUDE.md rule). No
  file is deleted by this plan.
- **Run `./install.sh` before any live check.** `ccs` runs the installed copy.
- Baseline before starting: `python -m pytest` → **442 passed**.
- Spec: `docs/superpowers/specs/2026-07-27-panel-settings-and-colour-sliders-design.md`

## File Structure

| file | change |
|---|---|
| `ccas/format.py` | HSL maths, `ACCOUNT` colour value, new `DEFAULT_FORMAT` and `DEFAULT_FORMAT_COLORS`, `_icon` reads hex |
| `ccas/registry.py` | hex `color`, int normalisation on load, display-mode migration on load, `add()` defaults |
| `ccas/label.py` | `render()` becomes a delegate |
| `ccas/paths.py` | `DISPLAY_MODES` removed |
| `ccas/panel.py` | `color_targets()`, `shows_color_row()` removed, `STAYS_OPEN` loses `display` |
| `ccas/panel_ui.py` | slider editor, Settings drawer, `Show` dropdown removed |
| `ccas/cli.py` | `ccs display` removed, `cmd_list` prints hex, `_color_menu` writes hex |
| `tests/test_*.py` | one file per module, as the repo already does |

---

### Task 1: The HSL maths

Pure arithmetic, no callers yet. First because everything visual depends on it
and it can be checked exactly.

**Files:**
- Modify: `ccas/format.py` (after `valid_color`, around line 30)
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `format.LIGHTNESS: float` = `0.78`
  - `format.hex_to_hs(value: str) -> tuple[float, float]` — degrees 0–360, saturation 0–1
  - `format.hs_to_hex(hue: float, saturation: float, lightness: float = LIGHTNESS) -> str` — `"#rrggbb"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_format.py`:

```python
def test_hex_to_hs_reads_the_palette_blue():
    """The two slider coordinates come out of a stored hex. #89b4fa is
    paths.PALETTE's blue, measured at H=217.2 S=91.9%."""
    hue, sat = fmt.hex_to_hs("#89b4fa")
    assert round(hue, 1) == 217.2
    assert round(sat, 3) == 0.919


def test_hs_to_hex_pins_lightness():
    """Saturation 0 is a grey, and the same grey at every hue: with L fixed
    there is no hue left to see. It is light enough to read on the bar's
    #353535, which is why lightness is pinned rather than offered."""
    assert fmt.hs_to_hex(0, 0.0) == "#c7c7c7"
    assert fmt.hs_to_hex(217.2, 0.0) == "#c7c7c7"


def test_hs_to_hex_round_trips_within_a_rounding_error():
    """8-bit channels cannot hold the exact angle back, so this is approximate
    by nature — a drag that moved nothing must not shift the colour visibly."""
    hue, sat = fmt.hex_to_hs(fmt.hs_to_hex(217.2, 0.919))
    assert abs(hue - 217.2) < 1.0
    assert abs(sat - 0.919) < 0.02


def test_hs_to_hex_wraps_hue_and_clamps_saturation():
    """The hue slider is a ring: 360 and 0 are the same colour, so a slider at
    either end must not produce two different reds."""
    assert fmt.hs_to_hex(360, 0.5) == fmt.hs_to_hex(0, 0.5)
    assert fmt.hs_to_hex(120, 1.5) == fmt.hs_to_hex(120, 1.0)
    assert fmt.hs_to_hex(120, -0.5) == fmt.hs_to_hex(120, 0.0)


def test_hs_to_hex_is_always_a_valid_color():
    """It feeds a pango attribute, so it has to satisfy the same gate a typed
    colour does."""
    for hue in range(0, 360, 37):
        assert fmt.valid_color(fmt.hs_to_hex(hue, 0.8))
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_format.py -k "hs" -q
```

Expected: FAIL, `AttributeError: module 'ccas.format' has no attribute 'hex_to_hs'`.

- [ ] **Step 3: Implement**

Add `import colorsys` to the imports at the top of `ccas/format.py`, then after
`valid_color`:

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

```bash
cd ~/ccas && python -m pytest tests/test_format.py -q
```

Expected: PASS, and the rest of `test_format.py` unchanged.

- [ ] **Step 5: Commit**

```bash
git add ccas/format.py tests/test_format.py
git commit -m "format: hue and saturation at the palette's fixed lightness"
```

---

### Task 2: The account colour becomes a hex string

**Files:**
- Modify: `ccas/registry.py:72-73` (`add`), `:120-137` (`set_field`), `:20-32` (`load`)
- Modify: `ccas/label.py:49`, `ccas/format.py:45` (`_icon`), `ccas/panel.py:66`
- Modify: `ccas/cli.py:364` (`cmd_list`), `:558-565` (`_color_menu`)
- Test: `tests/test_registry.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `format.HEX` (already exists, `format.py:22`)
- Produces: `account["color"]` is a `"#rrggbb"` string everywhere after this task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registry.py` (follow the file's existing fixture for a
scratch registry — every test here already goes through `CCAS_*` overrides):

```python
def test_add_assigns_an_unused_palette_hex(tmp_registry):
    """New accounts still walk the palette, but store what they picked rather
    than where it sat: paths.PALETTE is a table of starting points now, not an
    index space."""
    reg = {"default": None, "accounts": []}
    first = registry.add(reg, "a", "a@x", None)
    second = registry.add(reg, "b", "b@x", None)
    assert first["color"] == paths.PALETTE[0][1]
    assert second["color"] == paths.PALETTE[1][1]


def test_set_field_accepts_a_hex_colour(tmp_registry):
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    registry.set_field(reg, "a", "color", "#123abc")
    assert registry.find(reg, "a")["color"] == "#123abc"


def test_set_field_normalises_an_integer_colour(tmp_registry):
    """`ccs color <slug> 3` predates hex storage and still has to land."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    registry.set_field(reg, "a", "color", 3)
    assert registry.find(reg, "a")["color"] == paths.PALETTE[3][1]


def test_set_field_rejects_a_token_colour_value(tmp_registry):
    """auto/dim/account mean something for a token and nothing for the widget's
    own colour, so the widget validates against HEX and not valid_color."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    for bad in ("auto", "dim", "account", "#12", "blue"):
        with pytest.raises(ValueError):
            registry.set_field(reg, "a", "color", bad)


def test_load_normalises_an_integer_colour_written_by_the_old_code(tmp_registry):
    """A registry on disk from before this change is read, not rewritten: the
    normalisation is on the read path so nothing has to migrate at install."""
    _write_registry({"default": "a", "accounts": [
        {"slug": "a", "email": "a@x", "nickname": None, "color": 2,
         "hide_icon": False, "format": "%icon", "format_colors": {},
         "signal": 1},
    ]})
    reg = registry.load()
    assert reg["accounts"][0]["color"] == paths.PALETTE[2][1]
```

`_write_registry` and `tmp_registry` are whatever `tests/test_registry.py`
already uses — read the top of that file and reuse its helpers rather than
adding new ones.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_registry.py -q
```

Expected: FAIL — `add` still stores `0`, `set_field` still raises on a string.

- [ ] **Step 3: Implement**

`ccas/registry.py`, in `add()` replace lines 72-73:

```python
    used = {a["color"] for a in reg["accounts"]}
    color = next((h for _name, h in paths.PALETTE if h not in used),
                 paths.PALETTE[0][1])
```

In `set_field()` replace the colour branch (line 126-127):

```python
    if field == "color":
        # An int is the shape the pre-hex registry and `ccs color <slug> 3`
        # both use; it is normalised here rather than rejected, so the palette
        # keeps working as a set of shorthands.
        if isinstance(value, int) and not isinstance(value, bool):
            if not 0 <= value < len(paths.PALETTE):
                raise ValueError(f"invalid colour index: {value}")
            value = paths.PALETTE[value][1]
        # HEX, not valid_color: auto/dim/account are token values and mean
        # nothing for the widget's own colour.
        elif not (isinstance(value, str) and format.HEX.match(value)):
            raise ValueError(f"invalid colour: {value!r}")
```

In `load()`, inside the existing `for account in reg["accounts"]:` loop:

```python
        # Written as an index before colours became continuous. Normalised on
        # read so no pass over accounts.json is needed at install — rewriting a
        # whole registry to fix a field is the blind overwrite that "read the
        # live state before you write it" exists to prevent.
        if isinstance(account.get("color"), int):
            index = account["color"]
            account["color"] = paths.PALETTE[index][1] \
                if 0 <= index < len(paths.PALETTE) else paths.PALETTE[0][1]
```

`ccas/label.py:49` — replace:

```python
    color = account["color"]
```

`ccas/format.py:45` — replace:

```python
        attrs = f"color='{ctx['account']['color']}'"
```

`ccas/panel.py:66` — replace:

```python
        "color": a["color"],
```

`ccas/cli.py:364-366` — replace:

```python
        print(f"{star}{bang} {i}  {a['slug']:<14} {a['email']:<28} "
              f"{a['color']}")
```

`ccas/cli.py:558-565` — `_color_menu` becomes:

```python
def _color_menu(reg, slug: str, gui: bool) -> int:
    """The terminal keeps the eight presets. fzf cannot grow a slider, so this
    door offers the palette and the panel offers the continuous editor; the two
    doors have always differed in toolkit rather than in verb."""
    account = registry.find(reg, slug)
    rows = [f"{label.MARK_ON if account['color'] == h else label.MARK_OFF} {name}"
            for name, h in paths.PALETTE]
    choice = pickers.choose("color", rows)
    if choice is None:
        return 0
    return _mutate(slug, "color", paths.PALETTE[rows.index(choice)][1])
```

- [ ] **Step 4: Run the whole suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: the new tests PASS. Existing tests that assert an integer colour will
fail — update each to the palette hex. Do not weaken an assertion to make it
pass; change the expected value.

- [ ] **Step 5: Commit**

```bash
git add ccas tests
git commit -m "The account colour is a hex string, not a palette index"
```

---

### Task 3: The `account` colour value

**Files:**
- Modify: `ccas/format.py:21` (constants), `:25-29` (`valid_color`), `:37-52` (`_icon`), `:177-191` (`_emit`)
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: `account["color"]` as hex (Task 2)
- Produces: `format.ACCOUNT: str` = `"account"`, accepted by `valid_color`

- [ ] **Step 1: Write the failing tests**

```python
def test_account_is_a_valid_colour_value():
    assert fmt.valid_color("account")


def test_account_colours_a_token_with_the_widget_colour():
    """What makes the default's percentage follow the widget: one slider drag
    moves the glyph and the number together, with no hex baked into the
    registry to drift away from it."""
    account = _account(color="#89b4fa", format="%name",
                       format_colors={"%name": "account"}, nickname="n")
    out = fmt.render(account, 1)
    assert "color='#89b4fa'" in out


def test_account_colours_the_icon_like_auto_does():
    """auto already meant the account's colour for the glyph; account is the
    same answer said explicitly, so the two agree rather than compete."""
    account = _account(color="#f38ba8", format="%icon")
    explicit = fmt.render(dict(account, format_colors={"%icon": "account"}), 1)
    assert explicit == fmt.render(account, 1)
```

`_account(...)` is `tests/test_format.py`'s existing helper — read it and match
its signature rather than inventing one.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_format.py -k account -q
```

Expected: FAIL — `valid_color("account")` is False, so `_chosen` falls back to
`AUTO`.

- [ ] **Step 3: Implement**

`ccas/format.py` line 21:

```python
AUTO, DIM, ACCOUNT = "auto", "dim", "account"
```

`valid_color`:

```python
def valid_color(value) -> bool:
    """The only four shapes that may reach a pango attribute. This is what
    makes the format string layout rather than markup — nothing else a user
    types ever lands inside a span tag."""
    return value in (AUTO, DIM, ACCOUNT) or \
        bool(isinstance(value, str) and HEX.match(value))
```

In `_icon`, add a branch before the `else`:

```python
    elif chosen in (AUTO, ACCOUNT):
        attrs = f"color='{ctx['account']['color']}'"
```

(replacing the existing `elif chosen == AUTO:` branch — for the glyph the two
mean the same thing.)

In `_emit`, add before the `else`:

```python
    elif chosen == ACCOUNT:
        attrs = f"color='{ctx['account']['color']}'"
```

- [ ] **Step 4: Run the suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/format.py tests/test_format.py
git commit -m "format: an 'account' colour, so a token can follow the widget"
```

---

### Task 4: `custom` is the only display mode

The largest task, and the one to read the spec's migration paragraph before
starting.

**Files:**
- Modify: `ccas/label.py:33-70` (`render`), `ccas/paths.py:18-21` (`DISPLAY_MODES`)
- Modify: `ccas/registry.py` (`add`, `set_field`, `load`)
- Modify: `ccas/panel.py:30-32` (`STAYS_OPEN`), `:150` (`build_state`), `:169-172` (`shows_color_row`)
- Modify: `ccas/cli.py:265` (`dispatch_panel`), `:548-555` (`_display_menu`), `:677`, `:753-754`
- Modify: `ccas/panel_ui.py:819-852` (the `Show` dropdown), `:905-` (`_on_mode`)
- Test: `tests/test_label.py`, `tests/test_registry.py`, `tests/test_panel.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `format.DEFAULT_FORMAT` (Task 5 changes its value; this task does not depend on which string it is)
- Produces: no account carries a `display` key after its next write; `label.render` delegates unconditionally.

- [ ] **Step 1: Write the failing tests**

`tests/test_label.py` — replace the four mode tests with:

```python
def test_render_delegates_every_account_to_the_format(monkeypatch):
    """One mode means one code path: the label is whatever the format string
    says, and 'icon only' is now the string '%icon' rather than a mode."""
    account = _account(format="%icon", color="#89b4fa")
    assert label.render(account, 1) == fmt.render(account, 1)
```

`tests/test_registry.py`:

```python
def test_load_migrates_an_account_off_a_retired_display_mode(tmp_registry):
    """Its stored format was never rendered — the mode decided the label — so
    it is replaced by the default rather than trusted."""
    _write_registry({"default": "a", "accounts": [
        {"slug": "a", "email": "a@x", "nickname": None, "color": "#89b4fa",
         "display": "nickname", "hide_icon": False,
         "format": registry.OLD_DEFAULT_FORMAT, "format_colors": {},
         "signal": 1},
    ]})
    account = registry.load()["accounts"][0]
    assert "display" not in account
    assert account["format"] == format.DEFAULT_FORMAT
    assert account["format_colors"] == format.DEFAULT_FORMAT_COLORS


def test_load_keeps_a_format_the_user_typed(tmp_registry):
    """Only a format still equal to the old default is assumed unchosen. One
    the user actually wrote survives the migration — a value we did not capture
    is a value we cannot restore."""
    _write_registry({"default": "a", "accounts": [
        {"slug": "a", "email": "a@x", "nickname": None, "color": "#89b4fa",
         "display": "index", "hide_icon": False,
         "format": "%index %7d", "format_colors": {}, "signal": 1},
    ]})
    account = registry.load()["accounts"][0]
    assert "display" not in account
    assert account["format"] == "%index %7d"


def test_load_leaves_an_account_already_on_custom_alone(tmp_registry):
    _write_registry({"default": "a", "accounts": [
        {"slug": "a", "email": "a@x", "nickname": None, "color": "#89b4fa",
         "display": "custom", "hide_icon": False,
         "format": "%icon %name", "format_colors": {"%name": "dim"},
         "signal": 1},
    ]})
    account = registry.load()["accounts"][0]
    assert "display" not in account
    assert account["format"] == "%icon %name"
    assert account["format_colors"] == {"%name": "dim"}


def test_set_field_rejects_display(tmp_registry):
    """The field is retired; writing it would put a key back that load() strips."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    with pytest.raises(ValueError):
        registry.set_field(reg, "a", "display", "custom")
```

`tests/test_panel.py`:

```python
def test_build_state_has_no_display(monkeypatch, tmp_registry):
    state = panel.build_state("a")
    assert "display" not in state


def test_stays_open_does_not_carry_display():
    assert "display" not in panel.STAYS_OPEN
```

`tests/test_cli.py`:

```python
def test_display_command_is_gone(capsys):
    """An unknown command returns non-zero rather than silently doing nothing."""
    assert cli.main(["display", "a", "custom"]) != 0
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: FAIL on each of the new tests.

- [ ] **Step 3: Implement**

`ccas/label.py` — `render()` becomes:

```python
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
```

Everything from the old line 48 to the end of the function goes. `WARNING_TEXT`
stays if anything still references it — check with
`grep -rn WARNING_TEXT ccas tests` before removing it.

`ccas/paths.py` — delete the `DISPLAY_MODES` list and its comment (lines 18-21).

`ccas/registry.py` — add near the top, after the imports:

```python
# What DEFAULT_FORMAT was before the display modes were retired. A stored format
# still equal to it was never chosen by anyone — it is what add() wrote — so the
# migration may replace it. Anything else the user typed, and it survives.
OLD_DEFAULT_FORMAT = "%icon %name %5h"
```

In `add()`, drop the `"display": "nickname",` line.

In `set_field()`, replace the display branch with a rejection:

```python
    if field == "display":
        raise ValueError("display modes are retired; set 'format' instead")
```

In `load()`, inside the per-account loop, after the colour normalisation:

```python
        # The mode is retired. An account that was not on "custom" had its
        # label built by the mode, so its stored format is whatever add() wrote
        # and is replaced; one the user typed is kept. The key is dropped either
        # way, and only on the read path — nothing rewrites accounts.json here.
        if "display" in account:
            if account.pop("display") != "custom" and \
                    account.get("format", OLD_DEFAULT_FORMAT) == OLD_DEFAULT_FORMAT:
                account["format"] = format.DEFAULT_FORMAT
                account["format_colors"] = dict(format.DEFAULT_FORMAT_COLORS)
```

`ccas/panel.py` — remove `"display",` from `STAYS_OPEN`, remove the
`"display": account.get("display", "nickname"),` line from `build_state`, and
delete `shows_color_row()`.

`ccas/cli.py` — delete `_display_menu()`; change line 265 to
`if kind == "color":`; remove the `display` branch from `main()` (lines 753-754)
and the "display as" row from the settings menu around line 677. Read that menu
before editing — the row list and the branch that dispatches it are two places.

`ccas/panel_ui.py` — in `_build_toggles`, delete the `Show` label, the
`Gtk.DropDown` built from `paths.DISPLAY_MODES` and its `notify::selected`
connection; delete `_on_mode`; and replace both `if panel.shows_color_row(state):`
guards with unconditional code. `Edit format…` and the token colour row now
always appear.

- [ ] **Step 4: Run the suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: PASS. Roughly 59 lines across the test files mention `display` —
`grep -rn display tests/` and resolve each. Tests that asserted a mode's label
are deleted, not rewritten; tests that merely built an account dict with a
`display` key drop the key.

- [ ] **Step 5: Verify the live bar still renders**

```bash
cd ~/ccas && ./install.sh && ccs render vo-se-15th && ccs list && ccs doctor
```

Expected: a pango string, a list with hex colours and no mode column, and a
doctor that reports no new failures.

- [ ] **Step 6: Commit**

```bash
git add ccas tests
git commit -m "Retire the display modes; the format string is the only label"
```

---

### Task 5: The new default format

**Files:**
- Modify: `ccas/format.py:19` (`DEFAULT_FORMAT`), add `DEFAULT_FORMAT_COLORS`
- Modify: `ccas/registry.py` `add()` (`format_colors`)
- Test: `tests/test_format.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: `format.ACCOUNT` (Task 3)
- Produces: `format.DEFAULT_FORMAT_COLORS: dict`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_default_format_is_glyph_reset_and_remaining():
    assert fmt.DEFAULT_FORMAT == "%icon %5hreset %5hquotaleft"
    assert fmt.DEFAULT_FORMAT_COLORS == {
        "%5hreset": "dim", "%5hquotaleft": "account"}


def test_the_default_renders_glyph_and_percent_in_the_widget_colour():
    """The reset time recedes and the two coloured runs match, so changing the
    widget's colour moves the whole label rather than half of it."""
    account = _account(color="#89b4fa", format=fmt.DEFAULT_FORMAT,
                       format_colors=dict(fmt.DEFAULT_FORMAT_COLORS))
    out = fmt.render(account, 1, usage=_reading(five_hour=36), now=_NOW)
    assert out.count("color='#89b4fa'") == 2
    assert f"alpha='{usage.DIM}'" in out
```

`_reading(...)` and `_NOW` are `tests/test_format.py`'s existing helpers — read
the file and match what it already uses to build a usage reading.

```python
def test_add_writes_the_default_format_colours(tmp_registry):
    reg = {"default": None, "accounts": []}
    account = registry.add(reg, "a", "a@x", None)
    assert account["format"] == format.DEFAULT_FORMAT
    assert account["format_colors"] == format.DEFAULT_FORMAT_COLORS
    # A copy, not the shared constant: two accounts must not edit one dict.
    assert account["format_colors"] is not format.DEFAULT_FORMAT_COLORS
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_format.py tests/test_registry.py -q
```

Expected: FAIL — `DEFAULT_FORMAT` is still `"%icon %name %5h"`.

- [ ] **Step 3: Implement**

`ccas/format.py` line 19:

```python
DEFAULT_FORMAT = "%icon %5hreset %5hquotaleft"
# The glyph and the remaining percentage in the widget's colour, the reset time
# dim between them. ACCOUNT rather than a literal hex so the pair follows the
# hue slider instead of drifting away from it the first time it moves.
DEFAULT_FORMAT_COLORS = {"%5hreset": DIM, "%5hquotaleft": ACCOUNT}
```

(Place it after the `AUTO, DIM, ACCOUNT` line, which must come first.)

`ccas/registry.py` in `add()`:

```python
        "format_colors": dict(format.DEFAULT_FORMAT_COLORS),
```

- [ ] **Step 4: Run the suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: PASS. Tests asserting the old default string change to the new one.

- [ ] **Step 5: Commit**

```bash
git add ccas tests
git commit -m "The default label: glyph, reset time, remaining percentage"
```

---

### Task 6: One list of colour targets

`panel.py` decides what the dropdown offers; `panel_ui.py` renders the list it
is handed.

**Files:**
- Modify: `ccas/panel.py` (`build_state`, new `color_targets`)
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: `format.tokens_in` (exists), `format.DEFAULT_FORMAT`
- Produces:
  - `panel.color_targets(account: dict) -> list[dict]` — each `{"token": str|None, "label": str, "color": str, "chips": bool}`
  - `state["color_targets"]` carrying that list

`state["format_tokens"]` **stays** for the length of this task. `panel_ui.py:866`
and two tests in `tests/test_panel.py` still read it; removing it here would
leave the panel broken until Task 7, and every task has to end working. Task 7
deletes it once its last reader is gone.

`token` is `None` for the widget and the token string otherwise. `color` is the
hex to seed the sliders with — the account's colour for the widget, and for a
token whichever of `auto`/`dim`/`account`/hex it holds resolved to something the
sliders can show. `chips` is False for the widget and True for a token.

- [ ] **Step 1: Write the failing tests**

```python
def test_color_targets_lead_with_the_widget():
    account = {"color": "#89b4fa", "format": "%icon %name",
               "format_colors": {}}
    targets = panel.color_targets(account)
    assert targets[0] == {"token": None, "label": "the widget",
                          "color": "#89b4fa", "chips": False}


def test_color_targets_then_follow_the_format_string():
    """Left to right the way the label reads, de-duplicated, and only the
    tokens this account actually uses — a target for a token that is not on the
    bar is a setting that does nothing."""
    account = {"color": "#89b4fa", "format": "%icon %name %icon",
               "format_colors": {}}
    assert [t["token"] for t in panel.color_targets(account)] == \
        [None, "%icon", "%name"]


def test_a_token_on_auto_seeds_the_sliders_with_the_account_colour():
    """auto and account both resolve to the widget's colour, so the sliders
    open where the eye already is rather than at an arbitrary red."""
    account = {"color": "#f38ba8", "format": "%name",
               "format_colors": {"%name": "auto"}}
    assert panel.color_targets(account)[1]["color"] == "#f38ba8"


def test_a_token_with_a_hex_seeds_the_sliders_with_it():
    account = {"color": "#f38ba8", "format": "%name",
               "format_colors": {"%name": "#123abc"}}
    assert panel.color_targets(account)[1]["color"] == "#123abc"


def test_a_token_on_dim_seeds_the_sliders_with_the_account_colour():
    """dim is an alpha, not a hue — there is no colour in it to show, so the
    sliders start from the widget's and the dim chip stays lit."""
    account = {"color": "#f38ba8", "format": "%name",
               "format_colors": {"%name": "dim"}}
    assert panel.color_targets(account)[1]["color"] == "#f38ba8"


def test_build_state_carries_the_targets(tmp_registry):
    state = panel.build_state("a")
    assert state["color_targets"][0]["token"] is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_panel.py -k color_targets -q
```

Expected: FAIL, `module 'ccas.panel' has no attribute 'color_targets'`.

- [ ] **Step 3: Implement**

In `ccas/panel.py`, near `build_state`:

```python
# The target value that means the account's own colour rather than a token's.
WIDGET = "widget"


def color_targets(account: dict) -> list:
    """What the panel's one colour dropdown offers: the widget, then each token
    in this account's format string.

    A decision, so it lives here — panel_ui renders the list it is handed. The
    widget leads because it is what the tokens on `auto` and `account` point at,
    and it is the only entry that is always present.

    `color` is what the sliders should open at. dim has no hue to show and auto
    and account both resolve to the widget's, so all three seed from the widget
    — the sliders start where the eye already is instead of at an arbitrary red.
    """
    own = account.get("color") or paths.PALETTE[0][1]
    colors = account.get("format_colors") or {}
    targets = [{"token": None, "label": "the widget", "color": own,
                "chips": False}]
    for token in fmt.tokens_in(account.get("format") or fmt.DEFAULT_FORMAT):
        value = colors.get(token, fmt.AUTO)
        targets.append({
            "token": token,
            "label": token,
            "color": value if fmt.HEX.match(value or "") else own,
            "chips": True,
        })
    return targets
```

In `build_state`, **add** an entry beside the existing `"format_tokens"`:

```python
        "color_targets": color_targets(account),
```

Both are present after this task. `format_tokens` goes in Task 7, with its last
reader.

- [ ] **Step 4: Run the suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/panel.py tests/test_panel.py
git commit -m "panel: one list of colour targets, the widget first"
```

---

### Task 7: The slider editor

The GTK task. `panel_ui.py` decides nothing — it renders `color_targets` and
emits the actions `cli.dispatch_panel` already handles.

**Files:**
- Modify: `ccas/panel_ui.py` (`_build_token_colors` → `_build_color_editor`, and the account swatch strip in `_build_toggles`)
- Modify: `assets/menu.css`
- Test: `tests/test_panel_ui.py` if it exists, else the import-boundary tests in `tests/test_panel.py`

**Interfaces:**
- Consumes: `panel.color_targets`, `format.hex_to_hs`, `format.hs_to_hex`, `format.LIGHTNESS`
- Produces: `panel.Action("color", slug, "#rrggbb")` for the widget and
  `panel.Action("format_color", slug, (token, value))` for a token — both kinds
  already exist in `STAYS_OPEN` and in `cli.dispatch_panel`.
- Produces: `panel_ui._build_color_editor(state, pick)` — **Task 8 changes this
  signature to `(state, pick, ui_state)`**; build it with two parameters here.
- Removes: `state["format_tokens"]` and its `build_state` entry, now that
  `_build_token_colors` — its only reader outside the tests — is gone. Delete
  the two assertions in `tests/test_panel.py` that pin it (around lines 759-776);
  `panel.color_targets` is what they were guarding.

- [ ] **Step 1: Write the failing test**

The widget tree is not unit-testable, but the boundary is. Add to
`tests/test_panel.py` alongside the two existing import-boundary tests:

```python
def test_panel_ui_still_imports_without_pygobject(monkeypatch):
    """ccs statusline runs in every prompt of every session and must not need
    GTK. The sliders add gi calls; they belong inside show(), like the rest."""
    monkeypatch.setitem(sys.modules, "gi", None)
    importlib.reload(importlib.import_module("ccas.panel_ui"))
```

If `tests/test_panel.py` already has an equivalent, do not duplicate it — run it
and move on to Step 3.

- [ ] **Step 2: Run it**

```bash
cd ~/ccas && python -m pytest tests/test_panel.py -k import -q
```

Expected: PASS already (this is a guard, not a driver — the real verification
for this task is Step 5, on screen).

- [ ] **Step 3: Implement**

Replace `_build_token_colors` in `ccas/panel_ui.py` with:

```python
def _build_color_editor(state, pick):
    """One dropdown, two sliders, one preview. The two colour rows this replaces
    showed the same eight swatches under the word Colour and meant different
    things — the account's colour and one token's — which is what made the
    section unreadable.

    Nothing is parsed here: state["color_targets"] arrives decided.
    """
    from gi.repository import Gtk, Gdk

    slug = state["slug"]
    targets = state["color_targets"]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.add_css_class("ccas-color-editor")

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    top.append(Gtk.Label(label="Colour of", xalign=0))
    picker = Gtk.DropDown.new_from_strings([t["label"] for t in targets])
    top.append(picker)

    def target():
        return targets[min(picker.get_selected(), len(targets) - 1)]

    # A colour halfway through a drag is in neither set _load_tints() built the
    # CSS classes from, so _tint() would name a class that does not exist and
    # the swatch would show nothing. Its own provider, updated per motion.
    preview = Gtk.Box()
    preview.add_css_class("ccas-preview")
    provider = Gtk.CssProvider()
    preview.get_style_context().add_provider(
        provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    for name in (fmt.AUTO, fmt.DIM, fmt.ACCOUNT):
        chip = Gtk.Button(label=name)
        chip.add_css_class("ccas-color-chip")
        chip.connect("clicked", lambda _b, v=name: _commit(v))
        chips.append(chip)
    top.append(chips)
    top.append(preview)
    box.append(top)

    hue = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 360, 1)
    sat = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
    for scale, name in ((hue, "hue"), (sat, "saturation")):
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.add_css_class("ccas-slider")
        scale.add_css_class(f"ccas-slider-{name}")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        label_widget = Gtk.Label(label=name, xalign=0)
        label_widget.set_size_request(70, -1)
        row.append(label_widget)
        row.append(scale)
        box.append(row)

    def current_hex():
        return fmt.hs_to_hex(hue.get_value(), sat.get_value())

    def repaint(*_a):
        # The bar's own background, so an uncommitted preview is still honest
        # about what the label will look like once it lands.
        provider.load_from_string(
            f".ccas-preview {{ background: {current_hex()}; }}")

    def _commit(value=None):
        entry_value = current_hex() if value is None else value
        chosen = target()
        if chosen["token"] is None:
            pick(panel.Action("color", slug, entry_value))
        else:
            pick(panel.Action("format_color", slug,
                              (chosen["token"], entry_value)))

    def seed(*_a):
        """Slider positions for the selected target, without committing: setting
        a Gtk.Scale emits value-changed, and acting on it would write the
        registry every time the dropdown moved."""
        h, s = fmt.hex_to_hs(target()["color"])
        hue.set_value(h)
        sat.set_value(s)
        chips.set_visible(target()["chips"])
        repaint()

    hue.connect("value-changed", repaint)
    sat.connect("value-changed", repaint)
    picker.connect("notify::selected", seed)

    # Commit on release, never during the drag: usage.py's rule is write only on
    # a change with waybar.signal() riding on the write, and a live drag would
    # fire both on every motion event. Gtk.Scale has no "released" signal in
    # GTK4, so the pointer release comes from a gesture and the keyboard from a
    # key controller.
    for scale in (hue, sat):
        release = Gtk.GestureClick()
        release.connect("released", lambda *_a: _commit())
        scale.add_controller(release)
        keys = Gtk.EventControllerKey()
        keys.connect("key-released", lambda *_a: _commit() or False)
        scale.add_controller(keys)

    entry = Gtk.Entry(placeholder_text="#rrggbb", max_length=7, width_chars=8)
    # The escape hatch for a colour the sliders cannot reach: they hold
    # lightness at format.LIGHTNESS, and format_colors accepts any hex.
    entry.connect("activate", lambda e: _commit(e.get_text()))
    top.append(entry)

    seed()
    return box
```

In `_build_toggles`, delete the account swatch strip (the `Colour` label and its
eight buttons) and the call to `_build_token_colors`, and append
`_build_color_editor(state, pick)` once. Add `from . import format as fmt` to
`panel_ui.py`'s module-level imports if it is not already there — `format.py` is
pure and safe to import at module scope; only `gi` is not.

Append to `assets/menu.css`:

```css
/* The preview sits on the bar's own background rather than the panel's, so an
 * uncommitted colour is judged against what will actually draw it. */
.ccas-preview      { min-width: 22px; min-height: 22px; border-radius: 999px;
                     background: #353535; }
.ccas-color-editor { padding: 2px 0 8px 0; }
.ccas-color-editor > box > label,
.ccas-color-editor label { color: #9399b2; }
.ccas-slider trough { min-height: 8px; border-radius: 999px; }
.ccas-slider-hue trough {
  background: linear-gradient(to right, #f38ba8 0%, #f9e2af 17%, #a6e3a1 33%,
              #94e2d5 50%, #89b4fa 67%, #cba6f7 83%, #f38ba8 100%); }
.ccas-slider slider { min-width: 14px; min-height: 14px; border-radius: 999px;
                      background: #cdd6f4; }
```

Note `install.sh` copies `menu.css` **once** and never overwrites it — the live
`~/.config/ccas/menu.css` is the user's. Apply the same addition there by hand
for the live check, and say so rather than letting `install.sh` appear to have
done it.

- [ ] **Step 4: Run the suite**

```bash
cd ~/ccas && python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Verify on screen — this is the real test for this task**

```bash
cd ~/ccas && ./install.sh
(CCAS_PANEL_OUTPUT=HDMI-A-1 setsid ccs --gui vo-se-15th >/tmp/panel.log 2>&1 &)
sleep 3 && grim -o HDMI-A-1 /tmp/panel.png
```

`CCAS_PANEL_OUTPUT` is not optional here: it skips the pointer probe, which
never answers on an output the user is not already on. Read `/tmp/panel.log`.

Then drag a slider with the recipe from CLAUDE.md — `swaymsg seat - cursor
move` (never `set`, which teleports and delivers no motion), press, move,
release — and confirm three things: the preview follows the drag, the bar
repaints once on release and not during, and `accounts.json` gained exactly one
new colour value.

```bash
before=$(stat -c %Y ~/.claude/.credentials.json)
# ... the drag ...
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo BUG
find ~/.claude -maxdepth 1 -type l   # must stay empty
```

To kill the panel, `pgrep -f` for the pid and kill that — `pkill -f "ccs --gui"`
matches the shell you typed it in.

- [ ] **Step 6: Commit**

```bash
git add ccas/panel_ui.py assets/menu.css tests
git commit -m "panel: one colour editor, hue and saturation on sliders"
```

---

### Task 8: The Settings drawer

**Files:**
- Modify: `ccas/panel_ui.py` (`_build_header`, `show`'s root assembly, `_build_toggles`)
- Modify: `assets/menu.css`
- Test: manual, on screen — this task moves widgets and adds no decisions

**Interfaces:**
- Consumes: the `Gtk.Revealer` already built in `show()` (`panel_ui.py:603`)
- Produces: nothing other modules use

- [ ] **Step 1: Move the state that the rebuild destroys**

Before touching the layout: `refresh_toggles()` tears down and rebuilds the whole
toggles box on every applied setting, so anything stored inside it is lost the
moment a checkbox is ticked. Two things now need to survive that — the drawer's
expanded flag and the colour editor's selected target. Put both in a dict in
`show()`'s scope, beside the existing `filling_toggles`:

```python
    # Outside the rebuilt subtree, for the same reason the search text and the
    # selected session are: refresh_toggles() destroys everything inside it.
    ui_state = {"expanded": False, "target": 0}
```

`_build_color_editor` gains a third parameter — it becomes
`_build_color_editor(state, pick, ui_state)`, and `_build_toggles` grows one too
so it can pass it down. Seed the dropdown with
`picker.set_selected(ui_state["target"])` immediately after building it, and
write back in the `notify::selected` handler:

```python
    picker.set_selected(min(ui_state["target"], len(targets) - 1))

    def on_target(_d, _p):
        ui_state["target"] = picker.get_selected()
        seed()
```

`set_selected` emits `notify::selected`, so the seeding runs `seed()` once on
build — which is harmless, because `seed()` only moves sliders and never
commits. That separation is why `seed` and `_commit` are two functions.

- [ ] **Step 2: Build the disclosure row**

In `_build_header`, delete the gear button (`panel_ui.py:496` and the lines that
build it). Add a new function:

```python
def _build_settings_row(state, reveal_toggle, ui_state):
    """A labelled disclosure, not a gear. It hides three account verbs and every
    widget setting, and an unlabelled icon is a guess about which."""
    from gi.repository import Gtk

    button = Gtk.Button()
    button.add_css_class("ccas-settings-row")
    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    arrow = Gtk.Label(label="⌄" if ui_state["expanded"] else "›")
    arrow.add_css_class("ccas-settings-arrow")
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    title = Gtk.Label(label="Settings", xalign=0)
    title.add_css_class("ccas-settings-title")
    hint = Gtk.Label(label="Account and widget customisation", xalign=0)
    hint.add_css_class("ccas-settings-hint")
    text.append(title)
    text.append(hint)
    inner.append(arrow)
    inner.append(text)
    button.set_child(inner)

    def toggled(_b):
        ui_state["expanded"] = not ui_state["expanded"]
        arrow.set_label("⌄" if ui_state["expanded"] else "›")
        reveal_toggle()

    button.connect("clicked", toggled)
    return button
```

The two glyphs are drawn by the panel's own stylesheet, not Waybar's, so the
FontAwesome rule does not apply here — but check them on screen anyway in
Step 5.

- [ ] **Step 3: Reassemble the root**

In `show()`, the order below the panes becomes:

1. `skip permissions` — stays outside the drawer, because it changes how the
   session this panel is about to launch will run and its state should be
   visible before pressing New session.
2. the Settings row from Step 2
3. the revealer, whose child is now a box holding: `headless runner`,
   `hide the icon`, `Edit format…`, the colour editor, and the manage verbs the
   revealer already carried.

`headless` goes inside because it only affects `ccs -p` from a terminal and is
never relevant to a panel click.

Set `revealer.set_reveal_child(ui_state["expanded"])` after building, so a
rebuild restores the drawer rather than closing it under the user's hand.

- [ ] **Step 4: Style it**

Append to `assets/menu.css`:

```css
.ccas-settings-row   { padding: 8px 16px; border-radius: 8px;
                       background: transparent; border: none; }
.ccas-settings-row:hover { background: rgba(255, 255, 255, 0.06); }
.ccas-settings-title { color: #cdd6f4; }
.ccas-settings-hint  { color: #7f849c; font-size: 0.82em; }
.ccas-settings-arrow { color: #7f849c; }
```

- [ ] **Step 5: Verify on screen**

```bash
cd ~/ccas && ./install.sh
(CCAS_PANEL_OUTPUT=HDMI-A-1 setsid ccs --gui vo-se-15th >/tmp/panel.log 2>&1 &)
sleep 3 && grim -o HDMI-A-1 /tmp/panel.png
```

Check four things:

- collapsed, the card is chips, usage, verbs, search, panes, skip permissions,
  Settings;
- expanding and then ticking `hide the icon` leaves the drawer **open** and the
  colour dropdown on the same target — this is the Step 1 state, and it is the
  bug this task is most likely to ship;
- the expanded card fits on **both** outputs. `DP-1` is x 0–2560 and
  `HDMI-A-1` is x 2560–4480 and they are not the same size; repeat the capture
  with `CCAS_PANEL_OUTPUT=DP-1`.
- Escape still closes the panel with the drawer open.

- [ ] **Step 6: Commit**

```bash
git add ccas/panel_ui.py assets/menu.css
git commit -m "panel: a labelled Settings drawer, not an unlabelled gear"
```

---

### Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/why.md`, `docs/waybar-setup.md` (if it names the modes)
- Modify: the spec — mark it shipped

- [ ] **Step 1: Update `CLAUDE.md`**

The module table's `format.py` row says "the `custom` display mode" — it is now
the only mode. The `label.py` row says "the bar label" and stays. Remove
`ccs display` from the Commands block; `ccs color <slug> <hex>` replaces
`ccs color <slug> <n>`.

- [ ] **Step 2: Add a `docs/why.md` section**

Only for what was found on the real system during Tasks 7 and 8 — the file holds
no status and no test counts, which is why it survives without maintenance. If
the drag, the preview provider, or the drawer's surviving state behaved
differently than this plan predicted, that is what goes in. If nothing surprised
you, add nothing.

- [ ] **Step 3: Mark the spec shipped**

Change its `Status:` line to `**shipped 2026-07-27**` (or the real date).

- [ ] **Step 4: Verify and commit**

```bash
cd ~/ccas && python -m pytest -q && ./install.sh && ccs doctor
git add -A && git commit -m "Document the retired display modes and the colour editor"
```

---

## Self-review

**Spec coverage.** Every section of the spec maps to a task: display-mode
removal → Task 4, with `ccs list` and `STAYS_OPEN` folded in; the default format
→ Task 5; the `account` value → Task 3; the colour model → Task 1; hex storage
and the terminal presets → Task 2; the editor and its preview provider → Task 7;
the drawer and the split toggles → Task 8; the rebuild-destroys-state trap →
Task 8 Step 1, deliberately first in that task; the two-output height check →
Task 8 Step 5.

**Not covered, and deliberately.** The spec's "out of scope" note about a better
format-string editor gets no task.

**Type consistency, checked.** Three errors found and fixed on this pass:
`state["format_tokens"]` was removed in Task 6 while `panel_ui.py:866` still read
it, which would have left Task 6 shipping a broken panel — it now goes in Task 7
with its reader; `_build_color_editor` was `(state, pick)` in Task 7 and called
with three arguments in Task 8, now stated in both; and `panel.WIDGET` was
declared in Task 6's interface block and used by nothing, since the widget target
is `token: None`.

**One thing this plan decides that the spec left open.** The spec says a
non-custom account is given `DEFAULT_FORMAT`. Task 4 narrows that: only a format
still equal to `OLD_DEFAULT_FORMAT` is replaced, because a format the user typed
is a value we did not capture and cannot restore. If that is wrong, it is Task 4
Step 3 and one test that change.
