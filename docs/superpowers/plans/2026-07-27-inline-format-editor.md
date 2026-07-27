# Inline Format Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edit an account's format string inside the GTK panel — entry, live
rendered preview, and token chips that insert at the caret — instead of
spawning a terminal for it.

**Architecture:** `panel.py` gains `format_previewer(slug)`, a GTK-free closure
factory that renders arbitrary format text against the account's real usage
reading. `panel_ui.py` gains `_build_format_editor`, which owns the entry, the
preview label and the insert chips, and hands `panel.Action("format", slug,
text)` to the same `pick` callback every other setting uses. `format` joins
`panel.STAYS_OPEN` (applied without closing the panel) but not
`panel.NO_REBUILD` (the token set changed, so the colour chips must be rebuilt).
`cli.dispatch_panel` gains one branch that calls the existing `_mutate`; the
`edit_format` branch and its button are deleted.

**Tech Stack:** Python 3.14, stdlib only at runtime. GTK4 via PyGObject in
`panel_ui.py` only. pytest. No build step.

## Global Constraints

Copied from `docs/superpowers/specs/2026-07-27-inline-format-editor-design.md`
and `CLAUDE.md`. Every task's requirements implicitly include these.

- **Never write to `~/.claude`.** CCAS only reads it.
- **Tests must never touch real state.** Every path goes through `ccas/paths.py`
  and every one is `CCAS_*`-overridable. Use the existing fixtures.
- **`panel.py` imports no GTK**, ever. `test_panel_never_imports_gi` pins it.
  `panel_ui.py` imports `gi` inside functions, never at module scope.
- **`panel_ui` decides nothing it can be handed.** Wording and lists come from
  `panel.py`.
- **The panel gains no write path of its own.** Every action goes through
  `cli.dispatch_panel` into `_mutate` / `registry.set_field`.
- **Write only on a change**, with `waybar.signal()` riding on the write —
  `_mutate` → `_refresh` already does this.
- **Unknown tokens warn, never block.** A stored format naming an unknown token
  renders as its own name on the bar.
- **Never delete files; move them to `.claude_trash/`** in the CCAS checkout.
  (Nothing in this plan deletes a file — only code inside files.)
- **`ccs` runs the installed copy.** Re-run `./install.sh` before every live
  check.
- TDD, one commit per task, **never co-sign or co-author a commit**.

## File Structure

| file | change |
|---|---|
| `ccas/panel.py` | add `format_previewer()`; add `"format"` to `STAYS_OPEN` |
| `ccas/panel_ui.py` | add `_build_format_editor()` and the section heading; delete the "Edit format…" button |
| `ccas/cli.py` | add the `format` branch to `dispatch_panel`; delete the `edit_format` branch |
| `assets/menu.css` | style the new widgets |
| `tests/test_panel.py` | previewer, `STAYS_OPEN`/`NO_REBUILD` membership |
| `tests/test_cli.py` | the `format` action writes; `edit_format` is gone |
| `tests/test_panel_ui.py` | the commit rules and the insert chips |

`ccas/format.py`, `ccas/registry.py`, `ccas/label.py` and `ccas/usage.py` are
untouched. `ccs format <slug> --edit`, `_format_edit` and `pickers.prompt_edit`
stay exactly as they are — they are the terminal door.

---

### Task 1: `panel.format_previewer`

The pure half: render typed text against a real account without a widget in
sight.

**Files:**
- Modify: `ccas/panel.py` (add after `color_targets()`, before `verb_labels()`)
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: `registry.load()`, `registry.find()`, `registry.index_of()`,
  `usage.read()`, `fmt.render()`, `fmt.unknown_tokens()` — all existing.
- Produces: `panel.format_previewer(slug: str, now: float | None = None)`
  returning `preview(text: str) -> tuple[str, list[str]]` — `(pango markup,
  unknown tokens)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_panel.py`. The `reg` fixture is already defined at the
top of that file — a two-account registry in a scratch root.

```python
def test_the_previewer_renders_the_typed_text_not_the_stored_format(reg):
    """The panel's format entry previews what is being typed. Reading the
    stored format instead would show the label you are trying to leave."""
    registry.set_field(reg, "one", "format", "%name")
    registry.save(reg)
    preview = panel.format_previewer("one")
    markup, _unknown = preview("%email")
    assert "one@example.com" in markup
    assert "First" not in markup


def test_the_previewer_answers_the_unknown_tokens_of_the_typed_text(reg):
    """Warned about under the entry, never rejected: an unknown token renders
    as its own name on the bar, which is visible and self-explaining."""
    preview = panel.format_previewer("one")
    markup, unknown = preview("%name %bogus")
    assert unknown == ["%bogus"]
    assert "%bogus" in markup


def test_the_previewer_carries_the_token_colours_already_set(reg):
    """The preview is the widget, not the format string: a colour set on a
    token is the only preview of that colour there is."""
    registry.set_field(reg, "one", "format_colors", {"%email": "#89b4fa"})
    registry.save(reg)
    markup, _unknown = panel.format_previewer("one")("%email")
    assert "#89b4fa" in markup


def test_the_previewer_reads_the_account_once(reg, monkeypatch):
    """Built once per editor, called per keystroke. A registry read per
    keystroke is the thing this shape exists to avoid."""
    reads = []
    real = panel.registry.load
    monkeypatch.setattr(panel.registry, "load",
                        lambda: reads.append(1) or real())
    preview = panel.format_previewer("one")
    for text in ("%n", "%na", "%name"):
        preview(text)
    assert len(reads) == 1


def test_the_previewer_tolerates_an_unknown_slug(reg):
    """build_state() tolerates one — the bar and the registry can disagree for
    one click after an account is removed — and this must not be the thing
    that tracebacks instead."""
    markup, unknown = panel.format_previewer("gone")("%name")
    assert unknown == []
    assert isinstance(markup, str)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/ccas && python -m pytest tests/test_panel.py -k previewer -v`
Expected: FAIL — `AttributeError: module 'ccas.panel' has no attribute 'format_previewer'`

- [ ] **Step 3: Implement it**

In `ccas/panel.py`, after `color_targets()`:

```python
def format_previewer(slug: str, now=None):
    """A closure that renders arbitrary format text for this account.

    The registry, the account's index and its usage reading are read once,
    here, and the closure then costs a render — the panel's format entry calls
    it on every keystroke, and a registry read per keystroke is the shape this
    exists to avoid.

    It answers the markup *and* the unknown tokens of the same text, because
    both are shown under the same entry and neither is worth a second walk of
    the format string.

    An unknown slug answers an empty render rather than raising, for the reason
    build_state() tolerates one: the bar and the registry can disagree for a
    click after an account is removed.
    """
    now = time.time() if now is None else now
    reg = registry.load()
    account = registry.find(reg, slug) or {}
    index = registry.index_of(reg, slug)
    reading = usage.read(slug)

    def preview(text: str):
        return (fmt.render(account, index, reading, now, format_override=text),
                fmt.unknown_tokens(text))

    return preview
```

- [ ] **Step 4: Run the tests**

Run: `cd ~/ccas && python -m pytest tests/test_panel.py -v`
Expected: PASS, including `test_panel_never_imports_gi`.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas
git add ccas/panel.py tests/test_panel.py
git commit -m "panel: a previewer that renders the text being typed"
```

---

### Task 2: The `format` action, end to end in the non-GUI half

The action kind exists and writes; the terminal branch goes away. No widget yet.

**Files:**
- Modify: `ccas/panel.py:31-34` (`STAYS_OPEN`)
- Modify: `ccas/cli.py:282-283` (the `edit_format` branch)
- Test: `tests/test_panel.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `cli._mutate(slug, field, value) -> int` (existing).
- Produces: `panel.Action("format", slug, "<format text>")`, handled by
  `cli.dispatch_panel`, applied in place and followed by a rebuild.

- [ ] **Step 1: Write the failing tests**

In `tests/test_panel.py`, edit the existing
`test_a_setting_is_applied_without_shutting_the_panel` — add `"format"` to the
first loop's tuple and remove `"edit_format"` from the second:

```python
    for kind in ("headless", "dangerous", "hide_icon",
                 "color", "format_color", "format"):
        assert kind in panel.STAYS_OPEN
    for kind in ("new", "resume", "switch", "add", "rename", "remove"):
        assert kind not in panel.STAYS_OPEN
```

Then append to `tests/test_panel.py`:

```python
def test_a_format_change_rebuilds_the_colour_chips():
    """The chips are one per token in the format string, so the set of things
    you can colour changed. Without the rebuild, adding %7dused leaves no way
    to colour it until the panel is reopened. The sliders are exempt for the
    opposite reason — a rebuild mid-drag loses the grab."""
    assert "format" not in panel.NO_REBUILD
    assert "color" in panel.NO_REBUILD
    assert "format_color" in panel.NO_REBUILD
```

In `tests/test_cli.py`, **replace** `test_the_panel_cannot_set_the_format_itself`
(currently at line 1667) and `test_the_format_button_opens_a_terminal` (line
1675) with:

```python
def test_the_panel_sets_the_format_itself():
    """It used to spawn a terminal: the panel was gone by the time the prompt
    appeared, and the format was the last widget setting that left the panel to
    be changed."""
    make_account()
    assert cli.dispatch_panel(panel.Action("format", "work", "%name")) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name"


def test_the_format_action_signals_the_bar(monkeypatch):
    """Through _mutate, so _refresh runs — the same path `ccs format` takes.
    The panel gains no write path of its own."""
    make_account()
    seen = []
    monkeypatch.setattr(cli, "_mutate",
                        lambda *a: seen.append(a) or 0)
    cli.dispatch_panel(panel.Action("format", "work", "%name %5hused"))
    assert seen == [("work", "format", "%name %5hused")]


def test_the_format_action_stores_an_unknown_token_as_typed():
    """Warned about beside the entry, never rejected: it renders as its own
    name on the bar, which is visible where a refusal is not."""
    make_account()
    assert cli.dispatch_panel(panel.Action("format", "work", "%bogus")) == 0
    assert registry.find(registry.load(), "work")["format"] == "%bogus"


def test_the_edit_format_action_is_gone():
    """Nothing generates a terminal for the format any more. `ccs format
    <slug> --edit` stays as the terminal door — a TTY and an ssh session
    cannot run GTK — but it is reached by typing it."""
    with pytest.raises(ValueError):
        cli.dispatch_panel(panel.Action("edit_format", "work", None))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/ccas && python -m pytest tests/test_cli.py -k "format_action or panel_sets_the_format or edit_format_action" tests/test_panel.py -k format -v`

If that `-k` combination is awkward, run the two files whole; the point is
that the new assertions fail. Expected: FAIL — `dispatch_panel` raises
`ValueError: unknown panel action: format`, and `"format" not in STAYS_OPEN`.

- [ ] **Step 3: Implement it**

In `ccas/panel.py`, extend `STAYS_OPEN` and its comment:

```python
STAYS_OPEN = frozenset({
    "headless", "dangerous", "hide_icon", "color", "format_color", "format",
})
```

`NO_REBUILD` is left alone — `format` is deliberately absent, and the comment
above it already says why a rebuild is the normal case. Add one sentence to
that comment:

```python
# ... The editor is already showing what it just wrote, so
# there is nothing for a rebuild to tell it. `format` is not exempt: it changes
# which tokens exist, so the chip row below it is now wrong.
```

In `ccas/cli.py`, replace the `edit_format` branch (line 282-283) with:

```python
    if kind == "format":
        # The panel's own entry, not a terminal: the format is a widget
        # setting, and it is the last one that used to leave the panel to be
        # changed. `ccs format <slug> --edit` is still the terminal door.
        return _mutate(slug, "format", value)
```

Place it beside the other settings branches, above the `add` / `rename` /
`remove` terminal branches.

- [ ] **Step 4: Run the whole suite**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS. If `test_generated_commands_all_force_gui_mode` or the
passthrough test fails, you have touched something you should not have — this
task changes no generated command.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas
git add ccas/panel.py ccas/cli.py tests/test_panel.py tests/test_cli.py
git commit -m "panel: the format string is a setting, applied in place"
```

---

### Task 3: The format editor widget

**Files:**
- Modify: `ccas/panel_ui.py` — add `_build_format_editor()`; edit
  `_build_toggles()` (lines ~878-889) to drop the "Edit format…" button and
  append the editor.
- Test: `tests/test_panel_ui.py`

**Interfaces:**
- Consumes: `panel.format_previewer(slug)` from Task 1;
  `panel.Action("format", slug, text)` from Task 2; `fmt.TOKENS`, `fmt.NAMES`.
- Produces: `panel_ui._build_format_editor(state, pick, ui_state) -> Gtk.Box`.
  It reads `state["slug"]` and `state["format"]`, and uses
  `ui_state["focus_format"]` (a bool) to regrab focus after a rebuild.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_panel_ui.py`. The `gtk` fixture is at the top of the file
and skips when there is no display.

```python
def _format_editor(gtk, monkeypatch, stored="%email %5hused", ui_state=None):
    """(entry, chips, picked, ui_state). No window is mapped: the editor reads
    the dict it is handed and writes through the pick callback it is handed."""
    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None:
                        lambda text: (f"<span>{text}</span>",
                                      fmt.unknown_tokens(text)))
    picked = []
    ui_state = {"target": 0, "expanded": True} if ui_state is None else ui_state
    box = panel_ui._build_format_editor(
        {"slug": "vsed", "format": stored}, picked.append, ui_state)

    entries, chips = [], []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, gtk.Entry):
                entries.append(child)
            if isinstance(child, gtk.Button) and \
                    child.has_css_class("ccas-token-chip"):
                chips.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(box)
    return entries[0], chips, picked, ui_state


def _leave(entry):
    """The focus-out a click elsewhere would deliver. No window is mapped, so
    the controller is emitted on directly rather than moving a real focus."""
    controllers = entry.observe_controllers()
    for index in range(controllers.get_n_items()):
        controller = controllers.get_item(index)
        if isinstance(controller, Gtk.EventControllerFocus):
            controller.emit("leave")
            return
    raise AssertionError("no focus controller on the format entry")


def test_the_format_entry_is_seeded_with_the_stored_format(gtk, monkeypatch):
    entry, _chips, _picked, _ui = _format_editor(gtk, monkeypatch,
                                                 stored="%name %7dused")
    assert entry.get_text() == "%name %7dused"


def test_enter_commits_the_typed_format(gtk, monkeypatch):
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name %5hused")
    assert picked == []          # never per keystroke
    entry.emit("activate")
    assert picked == [panel.Action("format", "vsed", "%name %5hused")]


def test_focus_out_commits_the_typed_format(gtk, monkeypatch):
    """The safety net for a change that is finished but not entered."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    _leave(entry)
    assert picked == [panel.Action("format", "vsed", "%name")]


def test_an_unchanged_entry_writes_nothing(gtk, monkeypatch):
    """Opening the drawer, clicking into the entry and clicking out again is
    not a change — write only on a change, with the signal riding on it."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    _leave(entry)
    entry.emit("activate")
    assert picked == []


def test_a_committed_format_is_not_committed_twice(gtk, monkeypatch):
    """Enter, then the focus-out that follows reaching for another widget."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    entry.emit("activate")
    _leave(entry)
    assert picked == [panel.Action("format", "vsed", "%name")]


def test_a_token_chip_inserts_at_the_caret(gtk, monkeypatch):
    entry, chips, _picked, _ui = _format_editor(gtk, monkeypatch,
                                                stored="%name ")
    entry.set_position(6)
    chip = next(c for c in chips if c.get_child().get_label() == "email")
    chip.emit("clicked")
    assert entry.get_text() == "%name %email"


def test_a_token_chip_does_not_commit(gtk, monkeypatch):
    """A focusable chip steals focus, which is a focus-out, which would write
    the registry between every inserted token."""
    entry, chips, picked, _ui = _format_editor(gtk, monkeypatch)
    for chip in chips:
        assert chip.get_focusable() is False
    chips[0].emit("clicked")
    assert picked == []


def test_a_commit_asks_for_the_focus_back(gtk, monkeypatch):
    """The rebuild that follows destroys this entry. ui_state survives it, the
    same way the selected colour target does."""
    entry, _chips, _picked, ui_state = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    entry.emit("activate")
    assert ui_state["focus_format"] is True


def test_the_rebuilt_editor_takes_the_focus_flag_back(gtk, monkeypatch):
    """Read once and cleared, or every later rebuild — a ticked checkbox — would
    yank the focus into the format entry."""
    ui_state = {"target": 0, "expanded": True, "focus_format": True}
    _entry, _chips, _picked, ui_state = _format_editor(gtk, monkeypatch,
                                                       ui_state=ui_state)
    assert ui_state["focus_format"] is False
```

The helper `_leave` needs `Gtk` at module scope in the test file. Add to the
imports at the top of `tests/test_panel_ui.py`:

```python
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/ccas && python -m pytest tests/test_panel_ui.py -v`
Expected: FAIL — `AttributeError: module 'ccas.panel_ui' has no attribute
'_build_format_editor'`. If instead every test *skips*, you have no display —
export `WAYLAND_DISPLAY` from the user's session or run the suite from a
terminal inside it; a skipped test is not a passing one.

- [ ] **Step 3: Implement it**

In `ccas/panel_ui.py`, add above `_build_color_editor`:

```python
def _build_format_editor(state, pick, ui_state):
    """The format string, its preview, and a chip per token.

    The last widget setting that used to leave the panel: the button here
    spawned a terminal running `ccs format <slug> --edit`, because free text
    needs a prompt. It needs an entry, which the panel has — and the preview is
    better here than there, since this process can render the real pango.

    Commit is Enter or focus-out, never a settle timer. The sliders use one
    because every position a drag passes through is a colour; typing %5hused
    passes through %5, %5h and %5hus, each a valid but different format, so a
    pause mid-word would write the registry and repaint the bar with a
    half-typed label.
    """
    from gi.repository import Gtk

    slug = state["slug"]
    stored = state["format"]
    preview_of = panel.format_previewer(slug)
    # What was last written, so that opening the drawer and clicking out again
    # writes nothing: write only on a change is the same rule the hook obeys.
    committed = {"text": stored}

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.add_css_class("ccas-format-editor")

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    caption = Gtk.Label(label="format", xalign=0)
    caption.set_size_request(70, -1)
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.add_css_class("ccas-format-entry")
    entry.set_text(stored)
    row.append(caption)
    row.append(entry)
    box.append(row)

    # The widget, not the format string: it carries the ✻ exactly as the bar
    # does, which is also the only preview there is of a colour already set on
    # a token. Ellipsized, so a long format cannot widen the card.
    preview = Gtk.Label(xalign=0)
    preview.add_css_class("ccas-format-preview")
    preview.set_ellipsize(Pango.EllipsizeMode.END)
    box.append(preview)

    unknown_label = Gtk.Label(xalign=0)
    unknown_label.add_css_class("ccas-format-unknown")
    box.append(unknown_label)

    def repaint(*_a):
        markup, unknown = preview_of(entry.get_text())
        preview.set_markup(markup)
        # Hidden rather than blank: an empty row that appears and disappears
        # moves everything under it by its own height.
        unknown_label.set_visible(bool(unknown))
        if unknown:
            unknown_label.set_text("unknown token: " + " ".join(unknown))

    def commit(*_a):
        text = entry.get_text()
        if text == committed["text"]:
            return
        committed["text"] = text
        # The rebuild this triggers destroys the entry — the chip row below is
        # one chip per token and the token set just changed. The flag survives
        # it, the same way the selected colour target does.
        ui_state["focus_format"] = True
        pick(panel.Action("format", slug, text))

    entry.connect("changed", repaint)
    entry.connect("activate", commit)
    focus = Gtk.EventControllerFocus()
    focus.connect("leave", commit)
    entry.add_controller(focus)

    chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    chips.add_css_class("ccas-token-chips")
    insert_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    insert_caption = Gtk.Label(label="insert", xalign=0)
    insert_caption.set_size_request(70, -1)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scroller.set_hexpand(True)
    scroller.set_child(chips)
    insert_row.append(insert_caption)
    insert_row.append(scroller)

    def insert(token):
        # At the caret, not at the end: a token is as likely to belong in the
        # middle of the label as after it.
        position = entry.get_position()
        entry.get_buffer().insert_text(position, token, len(token))
        entry.set_position(position + len(token))

    for token in fmt.TOKENS:
        chip = Gtk.Button(label=fmt.NAMES[token])
        chip.add_css_class("ccas-token-chip")
        # Not focusable: a button takes the focus on click, which is a
        # focus-out, which would commit a half-built format between every
        # inserted token — the same mistake as committing a colour mid-drag.
        chip.set_focusable(False)
        chip.connect("clicked", lambda _b, t=token: insert(t))
        chips.append(chip)
    box.append(insert_row)

    repaint()
    # Read once and cleared: every applied setting rebuilds this subtree, and a
    # flag left standing would yank the focus here on a ticked checkbox.
    if ui_state.get("focus_format"):
        ui_state["focus_format"] = False
        entry.grab_focus()
        entry.set_position(-1)
    return box
```

`Pango` needs importing beside `Gtk` in this function:

```python
    from gi.repository import Gtk, Pango
```

Then in `_build_toggles`, replace the `bottom` block (the "Edit format…" button
and its `bottom.append` / `box.append(bottom)`) with the editor:

```python
    box.append(_build_format_editor(state, pick, ui_state))
    box.append(_build_color_editor(state, pick, ui_state))
```

Note `ui_state.get("focus_format")` — `show()` seeds `ui_state` with
`{"expanded": False, "target": 0}` and the key is only ever set by a commit, so
`get` is deliberate; do not add it to the seed.

- [ ] **Step 4: Run the tests**

Run: `cd ~/ccas && python -m pytest tests/test_panel_ui.py -v && python -m pytest`
Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas
git add ccas/panel_ui.py tests/test_panel_ui.py
git commit -m "panel: the format string is edited where its colours are"
```

---

### Task 4: The heading and the stylesheet

The drawer now holds a text field between two checkboxes with nothing saying
what it belongs to.

**Files:**
- Modify: `ccas/panel_ui.py` (`_build_toggles`)
- Modify: `assets/menu.css`
- Test: `tests/test_panel_ui.py`

**Interfaces:**
- Consumes: `_build_format_editor` from Task 3.
- Produces: nothing new; a heading above the format and colour editors.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_ui.py`:

```python
def test_the_widget_settings_carry_a_heading(gtk, monkeypatch):
    """A bare text field between two checkboxes says nothing about what it
    belongs to. The heading covers the format editor and the colour chips —
    they are one subject: what the widget says and how it is coloured."""
    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None: lambda text: (text, []))
    _visible, hidden = panel_ui._build_toggles(
        {"slug": "vsed", "format": "%name", "headless": False,
         "dangerous": False, "hide_icon": False,
         "color_targets": TARGETS},
        lambda _a: None, {"target": 0, "expanded": True})

    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, gtk.Label):
                found.append(child.get_label())
            walk(child)
            child = child.get_next_sibling()

    walk(hidden)
    assert "Bar label" in found
    assert "What this account's widget says, and how it is coloured" in found
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/ccas && python -m pytest tests/test_panel_ui.py -k heading -v`
Expected: FAIL — `assert 'Bar label' in [...]`.

- [ ] **Step 3: Implement it**

In `_build_toggles`, immediately before `box.append(_build_format_editor(...))`:

```python
    # A title and hint pair, reusing the Settings row's own classes rather than
    # inventing a second pair that would drift from it. It covers the format
    # editor and the colour chips: the format string decides which tokens the
    # chip row offers, so they are one subject and read top down.
    heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    heading.add_css_class("ccas-section-heading")
    title = Gtk.Label(label="Bar label", xalign=0)
    title.add_css_class("ccas-settings-title")
    hint = Gtk.Label(label="What this account's widget says, and how it is "
                           "coloured", xalign=0)
    hint.add_css_class("ccas-settings-hint")
    heading.append(title)
    heading.append(hint)
    box.append(heading)
```

In `assets/menu.css`, append beside the existing `.ccas-color-editor` rules
(after the `.ccas-slider` block, before the `.ccas-settings-*` block):

```css
/* The format editor. Its entry inherits the .ccas-color-editor entry rule's
 * shape deliberately — one text field in this drawer, styled one way. */
.ccas-format-editor { padding: 2px 0 6px 0; }
.ccas-format-editor > box > label,
.ccas-format-editor > label { color: #9399b2; }
.ccas-format-editor entry {
  min-height: 22px; padding: 2px 10px; border-radius: 8px;
  border: none; background: rgba(255, 255, 255, 0.06);
  font-family: monospace; font-size: 0.85em;
}
.ccas-format-editor entry:focus-within { background: rgba(255, 255, 255, 0.10); }
/* The preview is the bar's label, so it gets the bar's own background rather
 * than the panel's — a white token on #1e1e2e is not what the widget shows. */
.ccas-format-preview {
  background: #353535; border-radius: 6px; padding: 2px 8px;
  margin-left: 80px;
}
.ccas-format-unknown { color: #f9e2af; font-size: 0.82em; margin-left: 80px; }
/* The insert chips: quieter than the colour chips, which carry a value. */
.ccas-token-chip {
  min-height: 20px; padding: 1px 8px; border-radius: 8px;
  background: rgba(255, 255, 255, 0.06); color: #9399b2;
  border: none; font-size: 0.82em;
}
.ccas-token-chip:hover { background: rgba(255, 255, 255, 0.12); color: #cdd6f4; }
.ccas-section-heading { padding: 8px 0 2px 0; }
```

- [ ] **Step 4: Run the suite**

Run: `cd ~/ccas && python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/ccas
git add ccas/panel_ui.py assets/menu.css tests/test_panel_ui.py
git commit -m "panel: say what the bar label settings are"
```

---

### Task 5: Verify it on the real system

The suite proves the decisions; it does not prove the panel opens, the entry is
reachable, or that the stylesheet lands. Verify it yourself — never hand the
user a list of things to click.

**Files:** none. This task changes no code unless it finds a fault.

- [ ] **Step 1: Install, and confirm the invariant held**

```bash
cd ~/ccas
before=$(stat -c %Y ~/.claude/.credentials.json)
./install.sh
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo BUG
find ~/.claude -maxdepth 1 -type l    # must stay empty
ccs doctor; echo "rc=$?"
```

`ccs` runs the installed copy, not this checkout — a live check against an
un-installed change silently tests the old behaviour.

- [ ] **Step 2: Put the new stylesheet where the panel will read it**

`install.sh` copies `assets/menu.css` to `~/.config/ccas/menu.css` **once** and
never again — it is the user's after that. Back up the live one, then copy:

```bash
cp ~/.config/ccas/menu.css ~/.config/ccas/menu.css.bak-$(date +%s)
cp ~/ccas/assets/menu.css ~/.config/ccas/menu.css
```

Tell the user the backup's name at the end; they may have edited theirs.

- [ ] **Step 3: Ask which output is idle, then open the panel there**

Never map a window onto the output the user is working on. Waybar is on
`HDMI-A-1` (x 2560–4480); `DP-1` is x 0–2560. Ask the user which one is free
right now rather than assuming — it changes.

```bash
(CCAS_PANEL_OUTPUT=<connector> setsid ccs --gui vsed >/tmp/panel.log 2>&1 &)
sleep 3
grim -o <connector> /tmp/panel.png
```

`CCAS_PANEL_OUTPUT` skips the pointer probe, which an idle output would never
satisfy: the probe waits for `wl_pointer.enter` and the panel would open
nowhere at all.

- [ ] **Step 4: Read the screenshot**

Open the settings drawer first — click the "Settings" row with a relative
`cursor move` (never `cursor set`, which teleports and delivers no motion),
then `cursor press button1` / `release button1`, then re-`grim`. Check, in the
image: the "Bar label" heading and its hint, the format entry seeded with the
account's current format, the preview line rendering with the ✻ and the token
colours, and the insert chips.

- [ ] **Step 5: Drive one real edit end to end**

The panel's entry has the focus after a click into it. A **press** does reach
an idle output's layer surface, so a click works there; a drag does not, which
is why nothing here is a drag. Type a token with a uinput keyboard (a real
device, unlike a synthetic pointer) or click an insert chip, press Enter, then:

```bash
ccs format vsed            # the stored format, read back
grim -g "3420,0 220x22" /tmp/widgets.png
```

Diff **the widgets**, not the whole bar: `grim -g "2560,0 1920x24"` answers
CHANGED every time because the clock ticks beside it.

- [ ] **Step 6: Close the panel and restore what you touched**

Kill by process **name** first, never `pkill -f` / `pgrep -f` — those match the
shell running them, including the shell about to relaunch:

```bash
ps -eo pid,args | awk '/share\/ccas\/bin/ && !/awk/ {print $1}' \
  | while read p; do kill "$p"; done
```

Leave `~/.config/ccas/menu.css` as the new one if the check passed — that is
the point of copying it — and report the backup's path.

- [ ] **Step 7: Record anything reality contradicted**

If a live check disagreed with the plan — the focus-out never fires without a
mapped window, the chips overflow the card, the preview's background is wrong —
add a section to `docs/why.md` with the symptom and the cause, and fix the
code. That file takes bugs found on the real system and deviations from the
plan; it takes no status and no test counts.

- [ ] **Step 8: Commit anything the verification changed**

```bash
cd ~/ccas
git add -A
git commit -m "panel: <what the live check found>"
```

If nothing changed, there is nothing to commit — say so rather than making an
empty commit.

---

## Self-Review

**Spec coverage.** Heading → Task 4. Entry, preview, unknown line, insert chips,
`can_focus=False` → Task 3. Commit on Enter or focus-out, only on a change →
Task 3. The `format` action through `_mutate` → Task 2. `STAYS_OPEN` and not
`NO_REBUILD` → Task 2. The focus flag surviving the rebuild → Task 3. Button and
`edit_format` deleted → Tasks 2 and 3. `--edit` kept → stated in File Structure
and asserted by the untouched `test_format_edit_*` tests. `format_previewer` in
`panel.py`, `build_state` unchanged → Task 1. Every test the spec lists appears
in a task.

**Type consistency.** `format_previewer(slug, now=None)` → `preview(text) ->
(markup, unknown)` is defined in Task 1 and consumed with that shape in Task 3's
monkeypatch and in `_build_format_editor`. `_build_format_editor(state, pick,
ui_state)` matches its call in `_build_toggles` and in both test helpers.
`ui_state["focus_format"]` is written in Task 3's `commit` and read at the
bottom of the same function.
