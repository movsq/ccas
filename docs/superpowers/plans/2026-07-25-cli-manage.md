# CLI Account Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reach `add`, `rename` and `remove` from the interactive `ccs` picker, the way the Waybar menu already can.

**Architecture:** Entry points only. `cli.cmd_manage(action, slug, gui)` already implements all three actions and is called today only by Waybar-generated commands. Two changes in `ccas/cli.py`: the account picker gains an `Add account…` row (and stops being skipped when there is exactly one account), and `cmd_mode_menu` gains a `Manage…` row opening a second picker with rename and remove. No other module changes.

**Tech Stack:** Python 3.14, stdlib only. pytest. fzf (TTY picker) and fuzzel (GUI picker), both reached through `ccas/pickers.py`.

**Spec:** `docs/superpowers/specs/2026-07-25-cli-manage-design.md`

## Global Constraints

- **Do not modify `cmd_manage`, `cmd_add` or `cmd_rm`.** They already work. This plan only calls them.
- **Do not modify `ccas/menu.py` or `ccas/waybar.py`.** The Waybar menu already offers all of this, and no new command string is generated for Waybar — so the `--gui` invariant (`test_generated_commands_all_force_gui_mode`) is untouched and must stay passing.
- **Never let a non-launch row fall through to the launch dispatch.** `cmd_mode_menu` ends with `return launch.run(slug, "new", None, gui, None)` as the catch-all for `All projects…`; a new row tested after it silently starts a session. Test new rows next to the existing `headless_row` check, above `choice.startswith("New here")`.
- **Tests never touch real state.** `tests/test_cli.py`'s autouse `_isolate` fixture redirects every `CCAS_*` path to `tmp_path` and reloads the modules. Add tests to that file; do not add a fixture.
- **Never let a test really run `cmd_add`.** It shells out to `claude auth login` and rewrites the Waybar config. Stub `cli.cmd_add` and record the call.
- **Exact row strings**, used verbatim in code and tests:
  - `cli.ADD_ROW` = `"+  Add account…"` (ASCII `+`, two spaces, U+2026 ellipsis)
  - `"Manage…"`
  - `f"Rename {label.display_name(account)}…"`
  - `f"Remove {slug}…"`
- **ASCII `+` on purpose.** The vetted-glyph rule in `CLAUDE.md` is about Waybar's FontAwesome-first stack; this row is rendered by fzf/fuzzel, whose fonts nothing here has measured.
- **Cancel returns 1** from both pickers, matching `cmd_mode_menu` today.
- **One commit per task**, and **never co-sign or co-author** (global rule).
- Run the whole suite with `python -m pytest` (~206 tests, under a second) before each commit.

## File Structure

| File | Change |
|---|---|
| `ccas/cli.py` | Add `ADD_ROW` constant; rewrite the no-args tail of `cmd_tty` (lines 264-272); add a `Manage…` row and dispatch to `cmd_mode_menu` (lines 275-301); add `cmd_manage_menu(slug, gui)` next to `cmd_mode_menu` |
| `tests/test_cli.py` | Append the tests from both tasks |

Nothing else in the package is touched. `cmd_manage_menu` is its own function rather than an inlined block because `cmd_mode_menu` is already a build-rows/choose/dispatch unit and a second one nested inside it would obscure the dispatch-order rule above.

---

### Task 1: `Add account…` on the account picker

**Files:**
- Modify: `ccas/cli.py:264-272` (the no-args tail of `cmd_tty`), plus a module-level constant near `NO_ACCOUNT_ARGS` at `ccas/cli.py:17`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `cli.cmd_add(gui) -> int`, `cli.cmd_mode_menu(slug, gui) -> int`, `pickers.choose(prompt, rows, gui) -> str | None`, `label.display_name(account) -> str` — all existing.
- Produces: `cli.ADD_ROW: str`, the exact row string. Task 2 does not use it.

Current code, for reference:

```python
    if not reg["accounts"]:
        return cmd_add(gui)
    slug = reg["accounts"][0]["slug"]
    if len(reg["accounts"]) > 1:
        choice = pickers.choose("account", [label.display_name(a) for a in reg["accounts"]], gui)
        if choice is None:
            return 1
        slug = next(a["slug"] for a in reg["accounts"] if label.display_name(a) == choice)
    return cmd_mode_menu(slug, gui)
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_account_picker_offers_add_even_with_one_account(monkeypatch):
    """The picker used to be skipped when there was only one account — a
    one-row list asks nothing. Once it carries the Add row that stops being
    true, and skipping it puts Add out of reach for exactly the user most
    likely to want a second account."""
    make_account("work")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, g: seen.append(o) or o[0])
    monkeypatch.setattr(cli, "cmd_mode_menu", lambda slug, gui: 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert seen, "the picker was skipped"
    assert seen[0] == ["work", cli.ADD_ROW]


def test_choosing_the_add_row_adds_an_account(monkeypatch):
    """Not a slug, so the account lookup would raise StopIteration on it."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: cli.ADD_ROW)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: calls.append(gui) or 0)
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda *a: pytest.fail("add is not a launch"))

    assert cli.cmd_tty([], gui=False) == 0
    assert calls == [False], "gui is passed through to the login window"


def test_choosing_an_account_still_reaches_the_mode_menu(monkeypatch):
    """The common path. The Add row is compared before the account lookup, so
    this pins that it did not swallow real accounts."""
    make_account("work")
    make_account("other")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: o[1])
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda slug, gui: seen.append(slug) or 0)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: pytest.fail("no login"))

    assert cli.cmd_tty([], gui=False) == 0
    assert seen == ["other"]


def test_no_accounts_still_goes_straight_to_add(monkeypatch):
    """fuzzel exits instantly on empty stdin, so a picker whose only row is Add
    would be a dead end in GUI mode. With nothing to choose between, don't ask."""
    calls = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, g: pytest.fail("nothing to pick between"))
    monkeypatch.setattr(cli, "cmd_add", lambda gui: calls.append(gui) or 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert calls == [False]


def test_cancelling_the_account_picker_adds_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: None)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: pytest.fail("no login"))
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda *a: pytest.fail("no session"))

    assert cli.cmd_tty([], gui=False) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_cli.py -k "add_row or picker_offers_add or straight_to_add or cancelling_the_account_picker or still_reaches_the_mode_menu" -v
```

Expected: `AttributeError: module 'ccas.cli' has no attribute 'ADD_ROW'` on the tests that reference it; `test_account_picker_offers_add_even_with_one_account` fails because the picker is skipped (`seen` is empty).

- [ ] **Step 3: Add the constant**

Below `NO_ACCOUNT_ARGS` in `ccas/cli.py`:

```python
# The one row of the account picker that is not an account. ASCII '+' rather
# than a glyph: the vetted-glyph rule is about Waybar's FontAwesome-first stack,
# and this row is drawn by fzf or fuzzel, whose fonts nothing here has measured.
ADD_ROW = "+  Add account…"
```

- [ ] **Step 4: Rewrite the tail of `cmd_tty`**

Replace `ccas/cli.py:264-272` with:

```python
    if not reg["accounts"]:
        return cmd_add(gui)  # nothing to pick between, and fuzzel dies on an empty list
    # No shortcut for a single account any more: the list is no longer a
    # pointless one row, and skipping it hid Add from the user most likely to
    # want a second account.
    names = [label.display_name(a) for a in reg["accounts"]]
    choice = pickers.choose("account", [*names, ADD_ROW], gui)
    if choice is None:
        return 1
    if choice == ADD_ROW:  # before the lookup — it is not a display name
        return cmd_add(gui)
    slug = next(a["slug"] for a in reg["accounts"] if label.display_name(a) == choice)
    return cmd_mode_menu(slug, gui)
```

- [ ] **Step 5: Run the full suite**

```bash
cd ~/ccas && python -m pytest
```

Expected: all pass, including the five new tests. If an older test fails because it assumed the single-account shortcut, that assumption is what this task removes — update that test and say so in the commit body.

- [ ] **Step 6: Commit**

```bash
git add ccas/cli.py tests/test_cli.py
git commit -m "Offer 'Add account' on the terminal account picker"
```

---

### Task 2: `Manage…` in the mode menu

**Files:**
- Modify: `ccas/cli.py:275-301` (`cmd_mode_menu`), and add `cmd_manage_menu` immediately after it
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `cli.cmd_manage(action: str, slug: str | None, gui: bool) -> int` — existing, at `ccas/cli.py:232`. `registry.load()`, `registry.find(reg, slug)`, `label.display_name(account)`.
- Produces: `cli.cmd_manage_menu(slug: str, gui: bool) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_mode_menu_offers_manage(monkeypatch):
    """The three management actions existed as `ccs manage …` from the day they
    were written, but only a Waybar click ever called them."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return "Manage…" if prompt == "mode" else None

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)

    assert cli.cmd_mode_menu("work", False) == 1  # cancelled at the manage picker
    assert seen[0][-1] == "Manage…", "last, as it is in the Waybar menu"
    assert seen[1] == ["Rename work…", "Remove work…"], "no Add here"


@pytest.mark.parametrize("row, action", [("Rename work…", "rename"),
                                         ("Remove work…", "remove")])
def test_manage_menu_dispatches_with_the_slug(monkeypatch, row, action):
    """cmd_manage runs the real login/prompt/trash paths, so this pins the call
    rather than its effect."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: row)
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda a, s, g: calls.append((a, s, g)) or 0)

    assert cli.cmd_manage_menu("work", False) == 0
    assert calls == [(action, "work", False)]


def test_manage_menu_names_the_account_the_way_the_user_sees_it(monkeypatch):
    """Rename shows the nickname — that is what is on the bar and what is about
    to change. Remove shows the slug, because removal trashes the account
    directory and the slug is what that directory is called."""
    reg = registry.load()
    registry.add(reg, "me@x.com", "me@x.com", "work laptop")
    registry.save(reg)
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, g: seen.append(o) or None)

    assert cli.cmd_manage_menu("me@x.com", False) == 1
    assert seen[0] == ["Rename work laptop…", "Remove me@x.com…"]


def test_cancelling_the_manage_menu_manages_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: None)
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda *a: pytest.fail("cancel is not a command"))

    assert cli.cmd_manage_menu("work", False) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/ccas && python -m pytest tests/test_cli.py -k "manage_menu or mode_menu_offers_manage" -v
```

Expected: `AttributeError: module 'ccas.cli' has no attribute 'cmd_manage_menu'`, and `test_mode_menu_offers_manage` failing on `seen[0][-1]` — the last mode row is the headless row today.

- [ ] **Step 3: Add the `Manage…` row and its dispatch**

In `cmd_mode_menu`, extend the options list (`ccas/cli.py:286-287`):

```python
    options = [f"New here  ({short})", f"Resume last in  {short}",
               f"History in  {short}…", "All projects…", headless_row, "Manage…"]
```

and add the dispatch beside the existing headless check, **above** the
`startswith("New here")` chain:

```python
    # Before the launch rows: the last of those is this dispatch's catch-all.
    if choice == headless_row:
        _toggle_headless(reg, slug)
        return 0
    if choice == "Manage…":
        return cmd_manage_menu(slug, gui)
```

- [ ] **Step 4: Add `cmd_manage_menu`**

Immediately after `cmd_mode_menu`:

```python
def cmd_manage_menu(slug: str, gui: bool) -> int:
    """The terminal half of the Waybar menu's Manage submenu.

    Add is not here — it belongs to the account picker, which is the screen
    where the set of accounts is the subject. Rename is named after the
    nickname, which is what the user sees on the bar and what is about to
    change; Remove is named after the slug, because it trashes the account
    directory and the slug is what that directory is called.
    """
    account = registry.find(registry.load(), slug)
    rename = f"Rename {label.display_name(account) if account else slug}…"
    remove = f"Remove {slug}…"
    choice = pickers.choose("manage", [rename, remove], gui)
    if choice is None:
        return 1
    return cmd_manage("rename" if choice == rename else "remove", slug, gui)
```

- [ ] **Step 5: Run the full suite**

```bash
cd ~/ccas && python -m pytest
```

Expected: all pass. `test_mode_menu_still_launches_the_other_four_rows` indexes `o[0]`–`o[3]`, so a row appended at the end leaves it green — if it goes red, the row was spliced in rather than appended.

- [ ] **Step 6: Commit**

```bash
git add ccas/cli.py tests/test_cli.py
git commit -m "Offer rename and remove from the terminal mode menu"
```

---

### Task 3: Verify on the real system

**Files:**
- Modify: `CLAUDE.md` (the `## Commands` section), `docs/waybar-setup.md` only if it describes the terminal picker

The suite is stubbed at `pickers.choose` on both sides, so nothing so far has proved fzf renders these rows or that the live `ccs` reaches them. `ccs` runs the installed copy from `~/.local/share/ccas`, not this repo — a live check before `./install.sh` tests the old code.

- [ ] **Step 1: Install and audit**

```bash
cd ~/ccas && ./install.sh && ccs doctor
```

Expected: `ccs doctor` exits 0. If it was already failing before this work, note that and carry on — this task did not touch what it audits.

- [ ] **Step 2: Drive the real picker headlessly**

Do not ask the user to click anything (`docs/why.md` and the standing rule: automate the check). `script` gives the process a pty so `pickers.is_gui()` takes the **fzf** path, and fzf reads keystrokes from that pty — so the whole flow can be driven from a string. Point every path at a scratch sandbox first so the live registry is never touched:

```bash
SB=$(mktemp -d /tmp/claude-1000/-home-fixed-ccas/ccas-live.XXXX)
export CCAS_HOME=$SB/home CCAS_ACCOUNTS_ROOT=$SB/accts CCAS_CLAUDE_JSON=$SB/claude.json \
       CCAS_WAYBAR_CONFIG=$SB/config.jsonc CCAS_BASHRC=$SB/bashrc CCAS_TRASH=$SB/trash \
       CCAS_CLAUDE_BIN=$SB/fakeclaude CCAS_NO_RELOAD=1
mkdir -p "$CCAS_HOME/projects"; printf '{\n"modules-right": []\n}\n' > "$CCAS_WAYBAR_CONFIG"
printf '#!/bin/bash\ncase "$2" in login) : ;; status) echo "{\\"loggedIn\\":true,\\"email\\":\\"a@x.com\\"}";; esac\n' > "$SB/fakeclaude"
chmod +x "$SB/fakeclaude"
ccs add < /dev/null   # non-tty → no prompts; creates one account in the sandbox

script -qec "ccs" /dev/null <<< $'\e'          # Esc: cancel at the account picker
script -qec "ccs" /dev/null <<< $'Add\n\e'     # the Add row, then Esc out of add
```

Expected: the first run shows a two-row list whose second row is `+  Add account…` and exits 1. The second selects the Add row. Capture the output and read the rows — this is the only step that proves fzf renders the `+` and the `…` at all.

Repeat for the mode menu, selecting `Manage` and then cancelling:

```bash
script -qec "ccs" /dev/null <<< $'a@x.com\nManage\n\e'
```

Expected: the manage picker lists `Rename a@x.com…` and `Remove a@x.com…`, and Esc removes nothing (`ccs list` still shows the account). Then `rm -rf` is forbidden — move the sandbox aside: `mv "$SB" ~/.claude_trash/`.

- [ ] **Step 3: Confirm `~/.claude` was not written**

```bash
before=$(stat -c %Y ~/.claude/.credentials.json)
ccs list >/dev/null
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo BUG
find ~/.claude -maxdepth 1 -type l    # must stay empty
```

Expected: no `BUG`, no symlinks. A changed mtime is only a bug if it was ours — Claude Code running under the default account refreshes that token on its own.

- [ ] **Step 4: Update `CLAUDE.md`**

In the `## Commands` block, add a line documenting that the picker now carries management:

```
ccs                              # pick account → mode; Add is on the picker, Rename/Remove under Manage…
```

Do **not** add a section to `docs/why.md`: that file holds bugs found on the real system and deviations from a plan, and this is a routine feature. Add one only if a live check here contradicts the plan.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Document the picker's management entry points"
```
