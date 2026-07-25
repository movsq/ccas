# Retiring `menu.xml` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. (CCAS's CLAUDE.md records that the user declined subagent-driven development; work inline.) Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete Waybar's cached `menu-file` from CCAS so the bar stops reloading itself when a new Claude session appears, moving the bar's left click onto the fuzzel picker that already exists.

**Architecture:** Waybar parses `menu-file` once when a module is built and only `SIGUSR2` re-parses it, so keeping menu content fresh has meant periodically rebuilding the whole bar. `cli.cmd_mode_menu()` is already the same screen rendered through fuzzel/fzf, and `ccs --gui <slug>` already reaches it. Point `on-click` there, drop the three menu keys from the module block, resolve history from a live scan instead of a snapshot, and the reload has nothing left to invalidate.

**Tech Stack:** Python 3.14, stdlib only, pytest. No build step. `bin/ccs` is the entry point; `./install.sh` stages the package to `~/.local/share/ccas`.

**Spec:** `docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md`

## Global Constraints

- **stdlib only at runtime.** No new dependencies.
- **Never write to `~/.claude`.** CCAS reads it; symlinks live in the account directory and point at it.
- **Never delete a file — move it to `~/.claude_trash/`** (global CLAUDE.md rule). This plan removes two source files; both are moved, not `rm`'d.
- **Never invoke `claude` by bare name.** Always `paths.claude_bin()`.
- **`ccs doctor` never writes.**
- **Every Waybar-generated command carries `--gui`**, via `waybar._ccs()`. The `ccs -p` passthrough must not.
- **Tests must never touch real state.** Every path is `CCAS_*`-overridable.
- **`ccs` runs the installed copy.** Re-run `./install.sh` before any live check.
- **One commit per task. Never co-sign or co-author.**
- Run the full suite with `python -m pytest` from `~/ccas`; it takes under a second.

---

## File Structure

| File | Change |
|---|---|
| `ccas/label.py` | Gains `MARK_ON` / `MARK_OFF`, which outlive `menu.py`. |
| `ccas/cli.py` | `cmd_mode_menu` gains Display / Hide icon / Colour; `cmd_render` stops writing and reloading; `_refresh` signals instead of reloading; `_refresh_all` collapses to a save; `cmd_add` and the `config` branch stop writing menus. |
| `ccas/waybar.py` | `module_config` loses `menu`, `menu-file`, `menu-actions` and gains `on-click`. |
| `ccas/launch.py` | `_rows()` scans live; the `hist` mode goes. |
| `ccas/doctor.py` | The `menu.xml` / `history.tsv` presence check goes. |
| `ccas/paths.py` | `MENU_HIST_ITEMS` goes. |
| `ccas/menu.py` | Moved to `~/.claude_trash/`. |
| `tests/test_menu.py` | Moved to `~/.claude_trash/`. |
| `tests/test_label.py`, `test_cli.py`, `test_waybar.py`, `test_launch.py`, `test_doctor.py` | Follow the code above. |
| `CLAUDE.md`, `docs/waybar-setup.md`, `docs/why.md` | Four invariants struck; the Waybar caching facts stay. |

Task order keeps the bar coherent after every commit: the picker learns the missing rows *before* the click is redirected to it, and the click is redirected *before* history stops being a snapshot.

---

### Task 1: Move the mark glyphs to `label.py`

`MARK_ON`/`MARK_OFF` are the one part of `menu.py` that survives it — every remaining caller is a picker row, and picker rows are labels.

**Files:**
- Modify: `ccas/label.py`
- Modify: `ccas/menu.py:16-23`
- Modify: `ccas/cli.py` (every `menu.MARK_*` reference)
- Test: `tests/test_label.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `label.MARK_ON` (`"●"`, U+25CF), `label.MARK_OFF` (`"○"`, U+25CB). Every later task uses these names.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_label.py`:

```python
def test_the_state_marks_are_the_vetted_codepoints():
    """U+25CF/U+25CB, not ☑/☐. The bar's font stack starts with FontAwesome,
    which covers U+2611 but not U+2610, so a checkbox pair came from two fonts
    at two sizes and the checked state read as empty. Changing these means
    re-checking the new glyph against that exact stack with pango-view."""
    assert label.MARK_ON == "●"
    assert label.MARK_OFF == "○"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_label.py -k marks -v`
Expected: FAIL — `AttributeError: module 'ccas.label' has no attribute 'MARK_ON'`

- [ ] **Step 3: Move the constants**

In `ccas/label.py`, below `WARNING_TEXT`, add the constants together with the comment currently sitting above them in `menu.py:16-21` (the FontAwesome story is the reason they are these codepoints — it must not be lost in the move):

```python
# Every stateful row marks itself with this pair, radio or toggle. Not ☑/☐:
# the bar's font stack here starts with FontAwesome, which covers U+2611 but
# not U+2610, so the checked box came from FontAwesome and the unchecked one
# from DejaVu — different sizes, different weights, and the "on" state looked
# like an empty box on the bar. U+25CF/U+25CB were checked against that exact
# stack with pango-view and render consistently.
MARK_ON = "●"
MARK_OFF = "○"
```

In `ccas/menu.py`, replace the constant definitions and their comment block with an import, so the module keeps working until Task 6 removes it:

```python
from .label import MARK_ON, MARK_OFF  # noqa: F401 — re-exported until menu.py goes
```

- [ ] **Step 4: Repoint every caller**

```bash
grep -rn "menu\.MARK_\|MARK_ON\|MARK_OFF" ccas/ tests/
```

In `ccas/cli.py`, change `menu.MARK_ON` → `label.MARK_ON` and `menu.MARK_OFF` → `label.MARK_OFF` (`cmd_mode_menu`'s `headless_row` and `danger_row`). `label` is already imported there. In `tests/test_cli.py`, change `cli.menu.MARK_ON` → `cli.label.MARK_ON` and the same for `MARK_OFF` (five call sites, around lines 182, 470, 472, 492, 499).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS, one test more than before.

- [ ] **Step 6: Commit**

```bash
git add ccas/label.py ccas/menu.py ccas/cli.py tests/test_label.py tests/test_cli.py
git commit -m "Move the state marks to label.py, ahead of retiring menu.py"
```

---

### Task 2: Teach the picker Display, Hide icon and Colour

These three exist only in the GtkMenu today. They have to be in the picker *before* the click is redirected to it, or the redirect loses features.

**Files:**
- Modify: `ccas/cli.py:318-352` (`cmd_mode_menu`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `label.MARK_ON` / `label.MARK_OFF` (Task 1); `cli._mutate(slug, field, value) -> int`; `pickers.choose(prompt, rows, gui)`.
- Produces: `cli._display_menu(reg, slug, gui) -> int`, `cli._color_menu(reg, slug, gui) -> int`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`, next to the existing `test_mode_menu_offers_the_headless_runner_and_shows_its_state`:

```python
def test_mode_menu_offers_display_colour_and_hide(monkeypatch):
    """The three settings that only the Waybar menu could reach. Once the bar's
    click opens this picker instead of a GtkMenu, this is the only way in."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append((prompt, options))
        return None

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    cli.cmd_mode_menu("work", False)
    prompt, options = seen[0]
    assert "Display as…" in options
    assert "Color…" in options
    assert f"{cli.label.MARK_OFF} Hide icon" in options
    # The identity that used to head the menu. Once the bar's click opens this,
    # a picker prompted "mode" does not say which account it belongs to.
    assert prompt == "work"


def test_display_submenu_marks_the_current_mode_and_sets_the_new_one(monkeypatch):
    make_account("work")
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return "Display as…" if len(seen) == 1 else \
            next(o for o in options if o.endswith("index"))

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_ON} nickname" in seen[1], seen[1]
    assert f"{cli.label.MARK_OFF} index" in seen[1], seen[1]
    assert registry.find(registry.load(), "work")["display"] == "index"


def test_colour_submenu_marks_the_current_colour_and_sets_the_new_one(monkeypatch):
    make_account("work")
    target = paths.PALETTE[5][0]
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return "Color…" if len(seen) == 1 else \
            next(o for o in options if o.endswith(target))

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_ON} {paths.PALETTE[0][0]}" in seen[1], seen[1]
    assert registry.find(registry.load(), "work")["color"] == 5


def test_hide_icon_toggles_in_place_and_shows_its_state(monkeypatch):
    """A mark row like headless, not a submenu — there are only two states."""
    make_account("work")
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return next(o for o in options if "Hide icon" in o)

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert registry.find(registry.load(), "work")["hide_icon"] is True
    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_ON} Hide icon" in seen[1]
    assert registry.find(registry.load(), "work")["hide_icon"] is False


def test_a_cancelled_submenu_changes_nothing(monkeypatch):
    """Esc out of the second prompt must not fall through to a launch."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    calls = []

    def fake_choose(prompt, options, gui):
        calls.append(options)
        return "Color…" if len(calls) == 1 else None

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert registry.find(registry.load(), "work")["color"] == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_cli.py -k "display_submenu or colour_submenu or hide_icon_toggles or offers_display or cancelled_submenu" -v`
Expected: 5 FAIL — the rows are not in `options`, and `seen[1]` does not exist (IndexError).

- [ ] **Step 3: Implement**

In `ccas/cli.py`, add the two sub-prompts above `cmd_mode_menu`:

```python
def _display_menu(reg, slug: str, gui: bool) -> int:
    account = registry.find(reg, slug)
    rows = [f"{label.MARK_ON if account['display'] == m else label.MARK_OFF} {m}"
            for m in paths.DISPLAY_MODES]
    choice = pickers.choose("display as", rows, gui)
    if choice is None:
        return 0
    return _mutate(slug, "display", choice.split(" ", 1)[1])


def _color_menu(reg, slug: str, gui: bool) -> int:
    account = registry.find(reg, slug)
    rows = [f"{label.MARK_ON if account['color'] == i else label.MARK_OFF} {name}"
            for i, (name, _hex) in enumerate(paths.PALETTE)]
    choice = pickers.choose("color", rows, gui)
    if choice is None:
        return 0
    return _mutate(slug, "color", rows.index(choice))
```

In `cmd_mode_menu`, add the hide row beside `headless_row` and `danger_row`:

```python
    hide_row = (f"{label.MARK_ON if account and account['hide_icon'] else label.MARK_OFF}"
                " Hide icon")
```

and splice the three into `options` after `"All projects…"`, keeping the GtkMenu's grouping — launch verbs, appearance, runner toggles, manage:

```python
    options = [f"New here  ({short})", f"Resume last in  {short}",
               f"History in  {short}…", "All projects…",
               "Display as…", hide_row, "Color…",
               headless_row, danger_row, "Manage…"]
```

Then dispatch them **before** the launch rows — the last `return launch.run(...)` is this function's catch-all and will swallow anything added after it:

```python
    if choice == "Display as…":
        return _display_menu(reg, slug, gui)
    if choice == "Color…":
        return _color_menu(reg, slug, gui)
    if choice == hide_row:
        return _mutate(slug, "hide_icon", not account["hide_icon"])
```

Finally, change the picker's prompt from the literal `"mode"` to the account's
identity — the row that used to head the GtkMenu:

```python
    choice = pickers.choose(label.display_name(account), options, gui)
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest`
Expected: PASS. `test_mode_menu_still_launches_the_other_four_rows` is the one to watch — it pins that the catch-all does not swallow appended rows.

- [ ] **Step 5: Commit**

```bash
git add ccas/cli.py tests/test_cli.py
git commit -m "Give the account picker the three settings only the menu had"
```

---

### Task 3: Point the bar's click at the picker

**Files:**
- Modify: `ccas/waybar.py:31-63` (`module_config`)
- Test: `tests/test_waybar.py`

**Interfaces:**
- Consumes: `waybar._ccs()` → `"<ccs_bin> --gui"`.
- Produces: a module dict with keys `exec`, `interval`, `signal`, `tooltip`, `on-click` and no `menu*` key.

- [ ] **Step 1: Write the failing test**

In `tests/test_waybar.py`, replace `test_module_uses_absolute_paths_everywhere`, `test_module_declares_the_fixed_history_slots` and `test_module_carries_signal_and_menu_wiring` with:

```python
def test_module_uses_absolute_paths_everywhere():
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert mod["exec"].startswith("/home/fixed/.local/bin/ccs")
    assert mod["on-click"].startswith("/home/fixed/.local/bin/ccs")


def test_module_carries_signal_and_the_click(_isolate):
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert mod["signal"] == 1
    assert mod["interval"] == 30
    assert mod["on-click"].endswith("--gui work")


def test_the_module_declares_no_cached_menu():
    """Waybar parses menu-file once when the module is built and only SIGUSR2
    re-parses it, so a cached menu is a menu that has to be invalidated by
    rebuilding the whole bar. The click opens the picker instead, which is
    generated per click and cannot go stale."""
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert "menu" not in mod
    assert "menu-file" not in mod
    assert "menu-actions" not in mod
```

Update `test_generated_commands_all_force_gui_mode` to check the click, and delete `test_the_dangerous_action_is_generated_with_gui` (the dangerous row is a picker row now, pinned by Task 2's tests):

```python
def test_generated_commands_all_force_gui_mode():
    """--gui belongs on every waybar-generated command and nowhere else; the
    terminal entry point (`ccs -p …`) must be free to pick fzf. Pinned because
    the mistake was made once already, back when a bashrc function was the
    terminal path — see test_cli's passthrough gui test for the other half."""
    account = {"slug": "work", "signal": 1, "display": "index",
               "color": 0, "hide_icon": False}
    mod = waybar.module_config(account)
    assert "--gui" in mod["exec"]
    assert "--gui" in mod["on-click"]
    assert "--gui" in waybar.placeholder_config()["on-click"]
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_waybar.py -v`
Expected: FAIL — `KeyError: 'on-click'`, and the `menu`/`menu-file`/`menu-actions` assertions fail.

- [ ] **Step 3: Implement**

Replace the body of `waybar.module_config` with:

```python
def module_config(account: dict) -> dict:
    ccs = _ccs()
    return {
        "exec": f"{ccs} render {account['slug']}",
        "interval": 30,
        "signal": account["signal"],
        "tooltip": False,
        # Not a menu-file: Waybar parses that once when the module is built and
        # only a full SIGUSR2 reload re-parses it, so keeping a cached menu
        # fresh meant rebuilding the bar every time a session appeared. This
        # opens the same screen `ccs <slug>` opens in a terminal, generated per
        # click. --gui comes from _ccs(); stdin sniffing cannot be trusted here.
        "on-click": f"{ccs} {account['slug']}",
    }
```

The whole `actions` dict, the `hist-*` loop, the `ids` map and the `PALETTE` loop go with it.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/waybar.py tests/test_waybar.py
git commit -m "Open the picker on click instead of a cached GtkMenu"
```

---

### Task 4: Stop reloading the bar

**Files:**
- Modify: `ccas/cli.py:30-56` (`_refresh`, `_refresh_all`), `ccas/cli.py:148-172` (`cmd_render`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `waybar.signal(n)` — the per-module `SIGRTMIN+n` that repaints the label only.
- Produces: `_refresh(reg, account)` now signals; `cmd_render(slug)` prints and nothing else.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace `test_changing_a_setting_moves_the_radio_dot_in_the_menu`, `test_renaming_updates_the_menu_title_row` and `test_setting_the_headless_runner_rewrites_every_menu` with:

```python
def test_a_setting_change_signals_the_label_and_does_not_reload(monkeypatch):
    """The reload existed because the ●/○ marks were baked into a menu file
    Waybar caches. With no cached file, a label repaint is the whole job — and
    SIGRTMIN+n does that without rebuilding the bar."""
    make_account()
    reloads, signals = [], []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.waybar, "signal", lambda n: signals.append(n))

    assert cli.main(["color", "work", "5"]) == 0
    assert cli.main(["display", "work", "index"]) == 0
    assert cli.main(["nick", "work", "personal"]) == 0
    assert cli.main(["hide", "work", "toggle"]) == 0

    assert signals == [1, 1, 1, 1]
    assert reloads == [], "nothing here changes the set of modules"


def test_the_headless_toggle_touches_the_bar_not_at_all(monkeypatch):
    """It is not in the label — it decides which account answers `ccs -p`."""
    make_account("work")
    make_account("other")
    reloads, signals = [], []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.waybar, "signal", lambda n: signals.append(n))
    assert cli.main(["headless", "work"]) == 0
    assert registry.headless_slug(registry.load()) == "work"
    assert (reloads, signals) == ([], [])


def test_render_prints_the_label_and_never_reloads(monkeypatch):
    """The bug this whole change exists for: the first prompt of a new session
    created a jsonl, the next 30 s tick saw a uuid it had not cached, and the
    bar rebuilt itself while the user was typing."""
    make_account()
    reloads = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.history, "scan", lambda: [])
    assert cli.main(["render", "work"]) == 0

    from ccas.history import Session
    fresh = [Session(uuid="brand-new", cwd=str(paths.projects_root()), title="T",
                     mtime=9e9, path=None)]
    monkeypatch.setattr(cli.history, "scan", lambda: fresh)
    assert cli.main(["render", "work"]) == 0
    assert reloads == [], "a new session is not a reason to rebuild the bar"
```

- [ ] **Step 2: Run and watch them fail**

Run: `python -m pytest tests/test_cli.py -k "signals_the_label or headless_toggle_touches or never_reloads" -v`
Expected: FAIL — `reloads == [1, …]`, `signals == []`.

- [ ] **Step 3: Implement**

`_refresh` in `ccas/cli.py` becomes:

```python
def _refresh(reg, account) -> None:
    """Persist, warn if newly invisible, and repaint the label.

    A signal, not a reload: SIGRTMIN+n re-runs `exec` and updates the label,
    which since the menu stopped being a cached file is the only thing a
    setting change can alter on screen.
    """
    if label.check_invisible_warning(account):
        notify(label.WARNING_TEXT)
    registry.save(reg)
    waybar.signal(account["signal"])
```

`_refresh_all` becomes a save. Keep the function — three callers use it and the name still says what it means:

```python
def _refresh_all(reg) -> None:
    """Persist. `headless` is exclusive, so setting it on one account clears it
    on the others — but none of it reaches the bar: neither headless nor
    dangerous appears in the label, and there is no menu left to rewrite.
    """
    registry.save(reg)
```

`cmd_render` becomes:

```python
def cmd_render(slug: str) -> int:
    """Print the label. Nothing else — this runs every 30 s, per account."""
    reg = registry.load()
    account = registry.find(reg, slug)
    if account is None:
        return 1
    print(label.render(account, registry.index_of(reg, slug)))
    return 0
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest`
Expected: FAIL — `test_render_regenerates_the_menu_file` and the render-tick tests around `tests/test_cli.py:44-102` still expect a written `menu.xml`. Delete those three (`test_render_regenerates_the_menu_file`, the tick's write/no-write pair) — they pin a file that Task 6 removes, and their behaviour is now `test_render_prints_the_label_and_never_reloads`. Re-run: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/cli.py tests/test_cli.py
git commit -m "Signal the label instead of rebuilding the bar"
```

---

### Task 5: Resolve history from a live scan

**Files:**
- Modify: `ccas/launch.py:16-17` (`_rows`), `ccas/launch.py:58-88` (`resolve`)
- Test: `tests/test_launch.py`

**Interfaces:**
- Consumes: `history.scan() -> list[Session]` (newest first), `history.format_row(session, now) -> str`.
- Produces: `launch._rows() -> list[tuple[str, str, str]]` — `(label, uuid, cwd)`, capped at `paths.HIST_SLOTS`. No slug parameter: `~/.claude/projects` is shared across accounts.

- [ ] **Step 1: Write the failing tests**

In `tests/test_launch.py`, replace the `menu`-backed `seed()` with a live-scan stub and drop the `hist` tests:

```python
import ccas.history as history
# … remove `import ccas.menu as menu` and its importlib.reload line …


def seed(tmp_path, monkeypatch, n=2):
    now = time.time()
    sess = [
        Session(uuid=f"uuid-{i}", cwd=str(tmp_path / "proj"), title=f"T{i}",
                mtime=now - i, path=None)
        for i in range(n)
    ]
    monkeypatch.setattr(launch.history, "scan", lambda: sess)
    return sess


def test_last_uses_the_newest_live_session(tmp_path, monkeypatch):
    """No snapshot any more: the session you started a minute ago is the one
    this resumes, which under the cached menu it was not until the bar next
    reloaded."""
    seed(tmp_path, monkeypatch)
    _workdir, argv = launch.resolve("work", "last", None, True, None)
    assert argv == ["/usr/bin/true", "--resume", "uuid-0"]


def test_hist_mode_is_gone(tmp_path, monkeypatch):
    """It existed to resolve a GtkMenu item id (`hist-3`) against line 3 of a
    snapshot written at the same moment. With rows generated per click there is
    no id, no index and no snapshot."""
    seed(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        launch.resolve("work", "hist", "1", True, None)
```

Every other test in the file that calls `seed(tmp_path)` takes `monkeypatch` and calls `seed(tmp_path, monkeypatch)`. In `test_search_resolves_the_chosen_label`, replace `rows = menu.read_tsv("work")` with `rows = launch._rows()`. In `test_no_mode_carries_the_danger_flag_by_default` and `test_every_mode_carries_the_danger_flag_when_set`, drop `("hist", "0")` from both tuples — three modes remain.

- [ ] **Step 2: Run and watch them fail**

Run: `python -m pytest tests/test_launch.py -v`
Expected: FAIL — `seed()` takes the wrong arguments, and `hist` still resolves rather than raising.

- [ ] **Step 3: Implement**

In `ccas/launch.py`:

```python
def _rows():
    """(label, uuid, cwd) for the recent sessions, scanned live.

    ~/.claude/projects is shared across accounts, so this takes no slug. It was
    a per-account snapshot only because a GtkMenu item can carry a fixed action
    id and nothing else — `hist-3` had to mean line 3 of a file written at the
    same instant as the menu the user was looking at.
    """
    now = time.time()
    return [(history.format_row(s, now), s.uuid, s.cwd)
            for s in history.scan()[: paths.HIST_SLOTS]]
```

Add `import time` at the top of the module. Change `resolve`'s call site from `rows = _rows(slug)` to `rows = _rows()`, and delete the whole `if mode == "hist":` branch — the trailing `raise ValueError(f"unknown mode: {mode}")` then covers it.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/launch.py tests/test_launch.py
git commit -m "Resolve session history from a live scan, not a snapshot"
```

---

### Task 6: Retire `menu.py`

**Files:**
- Move: `ccas/menu.py`, `tests/test_menu.py` → `~/.claude_trash/`
- Modify: `ccas/cli.py` (`cmd_add`, the `config` branch, the `menu` import), `ccas/doctor.py:77-81`, `ccas/paths.py:27-28`
- Test: `tests/test_doctor.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_doctor.py`, delete `test_a_missing_menu_file_is_reported`, drop the three `menu.write(...)` calls and the `import ccas.menu as menu` line, and repoint the exit-code test at a fault that still exists:

```python
def test_the_command_exits_nonzero_only_when_something_failed(capsys):
    healthy()
    assert cli.main(["doctor"]) == 0
    (paths.account_dir("work") / "settings.json").unlink()
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "symlinks" in out
```

In `tests/test_doctor.py:74`, the expected-paths list holds `paths.account_dir("work") / "menu.xml"` — remove that element.

In `tests/test_cli.py`, delete `test_config_rebuilds_every_menu_even_when_the_session_set_is_unchanged` and `test_add_writes_the_menu_before_waybar_points_at_it`, and add in the latter's place:

```python
def test_add_registers_the_module_without_generating_any_files(monkeypatch):
    """`cmd_add` used to have to write menu.xml before waybar.apply pointed at
    it, because menu-file is read on the reload that follows. There is no
    menu-file, so there is no ordering rule left — the account directory holds
    symlinks and nothing generated."""
    _stub_login(monkeypatch, "someone@x.com")
    assert cli.main(["add"]) == 0
    directory = paths.account_dir("someone")
    assert directory.exists()
    assert not (directory / "menu.xml").exists()
    assert not (directory / "history.tsv").exists()
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_cli.py -k without_generating -v`
Expected: FAIL — `menu.xml` exists; `cmd_add` still writes it.

- [ ] **Step 3: Implement**

In `ccas/cli.py`: drop `menu` from the imports; delete the `menu.write(...)` line and its comment in `cmd_add` (`cli.py:246-249`); and in the `config` branch delete the `sessions = history.scan()` line and the `for account … menu.write(…)` loop, leaving:

```python
    if command == "config":
        # Rewrite the managed block and take the old bashrc function out. This
        # is what install.sh runs; it no longer rebuilds anything per account,
        # because nothing per account is generated any more.
        reg = registry.load()
        waybar.apply(reg)
        waybar.strip_bashrc()
        waybar.reload()
        return 0
```

In `ccas/doctor.py`, delete the `for filename in ("menu.xml", "history.tsv"):` loop and its three lines, and the now-unused `menu` import if present.

In `ccas/paths.py`, delete `MENU_HIST_ITEMS` and the comment above it. Keep `HIST_SLOTS`, updating its comment to say it now caps the picker's rows rather than a set of action ids.

Then move both files out, per the never-delete rule:

```bash
mkdir -p ~/.claude_trash
mv ccas/menu.py ~/.claude_trash/ccas-menu.py-2026-07-25
mv tests/test_menu.py ~/.claude_trash/ccas-test_menu.py-2026-07-25
```

- [ ] **Step 4: Catch every straggler**

```bash
grep -rn "menu\b" ccas/ tests/ bin/ install.sh uninstall.sh | grep -v "cmd_mode_menu\|cmd_manage_menu\|_display_menu\|_color_menu"
```

Expected: no hits in `ccas/` or `tests/`. Anything left is an import or a docstring reference to fix.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS. Note the new total — it replaces "~237 tests" in CLAUDE.md in Task 7.

- [ ] **Step 6: Commit**

```bash
git add -A ccas tests
git commit -m "Retire menu.py: no cached menu, nothing to generate per account"
```

---

### Task 7: Documentation, install, and verify it on the real bar

**Files:**
- Modify: `CLAUDE.md`, `docs/waybar-setup.md`, `docs/why.md`
- Modify: `docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md` (the unverified line, once measured)

- [ ] **Step 1: Strike the invariants that described the cache**

In `CLAUDE.md`, remove these four, which now describe nothing:
- "**Point Waybar at a menu only after writing it.**"
- "**Write and reload are one operation.**"
- "**`ccs render` no longer forces a rebuild**" (rewrite the `ccs config` line: it rebuilds the managed block, not menus)
- "**`session_set_changed()` is set-based on purpose.**"

Keep every measured Waybar fact above them — the caching behaviour is *why* the cache was retired, and the next person to reach for `menu-file` needs it. Add one line under the Waybar facts:

```markdown
CCAS no longer uses `menu-file` at all: the bar's click runs `ccs --gui <slug>`,
the same picker `ccs <slug>` opens in a terminal, generated fresh per click. The
facts above are why — a menu you cannot invalidate has to be invalidated by
rebuilding the bar. See `docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md`.
```

Update the module table (drop the `menu.py` row), the test count, and the `ccs config` line in the Commands block.

- [ ] **Step 2: Update `docs/waybar-setup.md`**

Anywhere it documents `menu-file`, `menu-actions` or the `hist-i` action ids as part of CCAS's module, mark that CCAS no longer emits them and why. Leave the styling half untouched — CCAS still does not own `style.css`.

- [ ] **Step 3: Install and confirm the generated config**

```bash
cd ~/ccas && ./install.sh
grep -A12 '"custom/cc-' ~/.config/waybar/config.jsonc | head -40
```

Expected: each module has `exec`, `interval`, `signal`, `tooltip`, `on-click`; no `menu`, `menu-file` or `menu-actions`.

- [ ] **Step 4: Prove the tick no longer reloads**

`ccs render` is what the bar runs every 30 s. Reload is `killall -SIGUSR2 waybar`, so shadow `killall` on PATH and watch:

```bash
D=$(mktemp -d); printf '#!/bin/sh\necho "RELOADED: $*" >> %s/hits\n' "$D" > $D/killall
chmod +x $D/killall
for slug in $(ccs list | awk '{print $3}'); do PATH="$D:$PATH" ccs render "$slug"; done
[ -s $D/hits ] && echo "BUG: still reloading" || echo "OK: no reload"
```

Expected: `OK: no reload`, and each `ccs render` printed a pango label.

- [ ] **Step 5: Verify the click path for real**

The click runs `ccs --gui <slug>`. Run exactly that and photograph the result — do not ask the user to click anything:

```bash
slug=$(ccs list | awk 'NR==1{print $3}')
ccs --gui "$slug" & sleep 1
grim -o DP-1 /tmp/claude-1000/*/scratchpad/picker.png
swaymsg -t get_tree | grep -i fuzzel
```

Expected: fuzzel is up, listing `New here`, `Resume last in`, `History in`, `All projects…`, `Display as…`, `Hide icon`, `Color…`, the two runner rows and `Manage…`. Read the PNG to confirm the rows render and the marks are not tofu. Then dismiss it (`pkill fuzzel`).

- [ ] **Step 6: Settle the one open question — does a mouse click select a fuzzel row?**

This is the spec's only accepted-but-unverified loss.

```bash
ccs --gui "$slug" & sleep 1
swaymsg seat - cursor set 1280 500      # over the picker on DP-1
swaymsg seat - cursor press button1
sleep 1; swaymsg -t get_tree | grep -ci fuzzel
```

If the window closed and something was selected, the mouse works and the spec's loss #2 is not a loss. If it did not, record that plainly. Either way, replace the "**unverified**" sentence in the spec's *Accepted losses* section with what was measured, and add a section to `docs/why.md` **only if** it turned out to be a regression — that file holds bugs found on the real system, not status.

- [ ] **Step 7: Audit the live install**

```bash
ccs doctor
before=$(stat -c %Y ~/.claude/.credentials.json)
ccs --gui "$slug" </dev/null; pkill fuzzel
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo "BUG: wrote to ~/.claude"
find ~/.claude -maxdepth 1 -type l
```

Expected: doctor exits 0 with no menu-file check in its output; the credentials mtime is unchanged; `find` prints nothing.

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "Record that CCAS no longer uses Waybar's menu-file"
```

---

## Self-Review

**Spec coverage.** Module block → Task 3. Three rows into the picker, and the prompt becoming the account identity → Task 2. Live history → Task 5. Reload table → Task 4. Deletions → Task 6. Placement (no positioning flags) → nothing to do, which is the point of choosing it. Retired CLAUDE.md rules → Task 7, Step 1. Accepted loss #2 → Task 7, Step 6.

**Placeholders.** None: every step names files, commands and expected output.

**Type consistency.** `label.MARK_ON`/`MARK_OFF` (Task 1) are used by Tasks 2 and 4. `_rows()` (Task 5) takes no argument and is called by `resolve` and by `test_search_resolves_the_chosen_label`. `_display_menu`/`_color_menu` (Task 2) take `(reg, slug, gui)` and return `int`, matching `_mutate`'s return.
