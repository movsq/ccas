# CLI account management — design

**Date:** 2026-07-25
**Status:** Approved design, ready for implementation planning

## Purpose

The Waybar menu ends with a `Manage` submenu — add, rename, remove. The terminal
picker has no equivalent: `ccs` lets you pick an account and then a launch mode,
and that is all. The three management actions already exist as
`ccs manage add|rename|remove`, fully implemented in `cli.cmd_manage()`, but the
only thing that ever calls them is a Waybar click. This adds the missing route
from the interactive picker.

No new management behaviour is written. This is entry points only.

## Non-goals

- **Appearance controls.** Display mode, hide-icon and colour are in the Waybar
  menu and not in the terminal picker. They stay that way; `ccs display`,
  `ccs hide` and `ccs color` already reach them from the command line.
- **Any change to `menu.py` or `waybar.py`.** The Waybar menu already offers all
  of this. No new command string is generated for Waybar, so the `--gui`
  invariant is untouched.
- **Any change to `cmd_manage` itself**, or to `cmd_add` / `cmd_rm`.

---

## 1. Account picker — `cmd_tty([])`

Today:

```python
if not reg["accounts"]:
    return cmd_add(gui)
slug = reg["accounts"][0]["slug"]
if len(reg["accounts"]) > 1:
    choice = pickers.choose("account", [...], gui)
```

The `len(...) > 1` shortcut skips the picker when there is exactly one account,
on the grounds that a one-row list asks nothing. Once the list carries an
`Add account…` row that is no longer true — with the shortcut in place, a
single-account user could never reach Add from the picker at all.

So: **whenever there is at least one account, show the picker**, with the
accounts followed by one extra row.

| choice | result |
|---|---|
| an account | `cmd_mode_menu(slug, gui)` — unchanged |
| `+  Add account…` | `cmd_add(gui)` |
| cancel (`None`) | return 1 — unchanged |

The zero-account case is unchanged: `cmd_add(gui)` directly, because there is
nothing to pick between.

The cost is one extra keystroke on every `ccs` for a user with a single account.
That was weighed and accepted; the screen being the same shape every time is
worth more than the keystroke.

### The row's text

`"+  Add account…"`, with an ASCII `+`.

The vetted-glyph rule in `CLAUDE.md` is about Waybar's font stack, where
FontAwesome wins any codepoint it covers. This row never reaches that stack — it
is rendered by fzf or fuzzel, which have font stacks of their own that nothing
in this repo has measured. A decorative glyph here would be an unmeasured
assumption for no gain.

Two spaces after the `+` so the row's text starts clear of the marker, matching
how `menu.MARK_ON`/`MARK_OFF` rows read in the mode menu.

### Matching the choice back

`pickers.choose` returns the row string. The existing code resolves an account
by comparing against `label.display_name(a)`. The Add row is compared first, as
an exact string equality against the module-level constant, before the account
lookup — so a nickname that happened to equal the row text cannot shadow it.

Define the row as a module-level constant in `cli.py` (`ADD_ROW`) rather than a
literal, so the test asserts against the same object the code offers.

## 2. Manage in the mode menu — `cmd_mode_menu`

One new row, `"Manage…"`, placed after `headless_row` — last in the list, as it
is in the Waybar menu.

Selecting it opens a second picker, prompt `manage`, with two rows:

- `Rename <display name>…` → `cmd_manage("rename", slug, gui)`
- `Remove <slug>…` → `cmd_manage("remove", slug, gui)`

No `add` row here. Add lives on the picker screen.

Rename names the account by `label.display_name(account)` — what the user sees
on the bar. Remove names it by **slug**, because removal trashes the account
directory and the slug is what that directory is called; a row that says
`Remove work…` when the directory is `me@example.com` invites removing the
wrong thing.

Cancelling this picker returns 1, matching how cancelling the mode menu behaves,
and mutates nothing.

### Dispatch order

`cmd_mode_menu` ends with

```python
return launch.run(slug, "new", None, gui, None)
```

as the catch-all for `All projects…`. Any row that is not a launch must be
tested **before** the launch rows, which is why `headless_row` is already
checked first. The `Manage…` test goes next to it, above the
`choice.startswith("New here")` chain. Getting this wrong does not raise — it
silently launches a session instead of managing the account.

### Why a second picker rather than flat rows

fzf and fuzzel are flat dmenu front-ends; a "submenu" here is a second prompt,
not a nested widget. Two extra rows could have gone straight into the mode menu
instead. They are nested because the Waybar menu nests them, and because the
mode menu is a list of things to *do with a session* — a destructive `Remove`
sitting one keystroke from `New here` is a worse list.

## 3. Structure

Both changes are in `cli.py`. The manage picker becomes its own function,
`cmd_manage_menu(slug, gui)`, next to `cmd_mode_menu` — same shape as its
neighbour: build rows, `pickers.choose`, dispatch, return an rc. It depends on
`pickers`, `registry`, `label` and `cmd_manage`, all of which `cli.py` already
imports.

Nothing else in the package changes.

## 4. Testing

`tests/test_cli.py` — `cli.py` owns the behaviour, and that file already stubs
`pickers.choose` for the existing `cmd_mode_menu` tests.

| test | pins |
|---|---|
| picker appears with a single account | the `len > 1` shortcut is really gone |
| picker rows include `ADD_ROW` | the entry point exists at all |
| choosing `ADD_ROW` calls `cmd_add` | and does *not* fall through to a mode menu |
| choosing an account still reaches the mode menu | the common path did not regress |
| zero accounts still goes straight to `cmd_add` | no empty picker |
| mode menu rows include `Manage…` | the entry point exists |
| Manage → rename dispatches `cmd_manage("rename", slug, …)` | right action, right slug |
| Manage → remove dispatches `cmd_manage("remove", slug, …)` | right action, right slug |
| cancelling the manage picker returns 1 and calls no manage action | cancel is not a launch |

Dispatch is asserted by stubbing `cli.cmd_manage` / `cli.cmd_add` and recording
the call, not by letting them run — `cmd_add` logs in and rewrites the bar.

Every test runs against the env-overridden scratch paths the file already sets
up. Nothing here reads or writes real state.
