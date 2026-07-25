# CCAS — session handoff

**Written:** 2026-07-25, after the initial build session
**State:** built, installed, and working on the real system. 158 tests green.

Paste this file's path into a new session and say "read HANDOFF.md and continue".

---

## Where things stand

All 12 planned tasks (0–11) are implemented and committed. The plan and spec are
still accurate except where §"Deviations" below says otherwise.

- Spec: `docs/superpowers/specs/2026-07-25-ccas-design.md`
- Plan: `docs/superpowers/plans/2026-07-25-ccas.md`
- Spike findings: `docs/superpowers/spike-waybar-menu.md`
- Live results: `docs/superpowers/task-11-live-integration.md`

Two real accounts are configured and confirmed working:

| slug | email | colour |
|---|---|---|
| `vo-se` | vo.sedlacek@gmail.com | 0 peach |
| `vsed` | vsedlacek1337@gmail.com | 1 red |

Both report `loggedIn: true` under their own `CLAUDE_CONFIG_DIR`.

**The central guarantee holds:** `~/.claude/.credentials.json` mtime has been
`1784954438` through every install, uninstall, reinstall and account add. CCAS
has never written to `~/.claude`.

Run the suite with `cd ~/ccas && python -m pytest` (158 passing, ~0.6 s).

---

## Verified live

- Placeholder module, add flow, OAuth login, both accounts isolated.
- Waybar modules render in `modules-center`: `✻ vo.se` peach, `✻ vsed` red.
- The menu opens with nested submenus: title row, New session, Resume last
  session, Resume from history ▸, Display as ▸, ☐ Hide icon, Color ▸, Manage ▸.
- Uninstall restores `config.jsonc` and `.bashrc` byte-for-byte (diffed).

- **Launching, all three paths:** New session (existing and freshly created
  directories), Resume last session, Resume from history, and the fuzzel search.
- Add, rename, remove from the Manage submenu. Hide icon.
- Display and colour switching, including the ●/○ markers moving.

## Not yet verified — start here

1. **Terminal front-end.** In a *new* shell (the `claude()` function is only in
   new shells): bare `claude` should show the account picker then the mode menu
   via **fzf**; `claude -p "say hi"` should run headless with no menu under the
   `default` account; `ccs <slug> claude auth status` should report that account.
   This is now the only wholly untested area.
2. **The rename dialog's new shape** — typing a nickname, selecting
   `⌫  clear nickname`, and Esc — was fixed and unit-tested but not yet clicked
   through on the real bar.
3. **`icon only` + `Hide icon`** should fire the notification exactly once, stay
   clickable while invisible, and re-arm after leaving the combination.

Note: `vo-se` was removed during testing, so the registry now holds one account
(`vsed`, colour renumbered to 0 peach, nickname cleared).

---

## Deviations from the written plan

These are all deliberate and committed. Do not "fix" them back.

| # | Change | Why |
|---|---|---|
| 1 | `waybar.reload()` lives in `cli.cmd_render`, not `menu.write()` | The spike found Waybar **caches** `menu-file`; the plan put the reload in `write()`, which would make every menu-writing test SIGUSR2 the live bar. Gated on `menu.session_set_changed()` so it does not flicker every 30 s tick. |
| 2 | `install.sh` moves the old staged copy to trash instead of `rm -rf` | Global CLAUDE.md: never delete. |
| 3 | Fixed the plan's `test_launch.py` seed helper | It passed `{"slug": "work"}` to `menu.write()`, which needs a full account dict. Plan-side bug. |
| 4 | Generated commands carry `--gui` | See "The is_gui bug" below. |
| 5 | Modules live in `modules-center`, no divider | User preference, changed twice: `modules-right` → end of `modules-left` with an em-dash → `modules-center` bare. `HOST_LIST` in `ccas/waybar.py` is the single knob. |
| 6 | Menu shows 15 history rows, not 300 | See "The oversized menu" below. |
| 7 | `New session` in a missing directory asks before creating it | See "Creating a project" below. |
| 8 | A setting change rewrites the menu and full-reloads | See "Stale ●/○ markers" below. |
| 9 | A cleared nickname shows nothing on the bar, not the email | User preference. `display_name()` keeps the fallback where identity matters. |

---

## Bugs found on the real system, and their fixes

### The `is_gui` bug (the important one)

`pickers.is_gui()` was `not sys.stdin.isatty()`. **Waybar inherits stdin from
the compositor, which on a TTY session is a real terminal (`/dev/tty1`).** So a
Waybar click reported "terminal", took the fzf/`input()` path, and blocked
forever reading a console the user cannot see. The placeholder appeared to do
nothing; a stuck `ccs manage add` process was found and killed.

Fix: every Waybar-generated command now starts with `--gui`, consumed in
`cli.main()`. `is_gui()` remains only as a fallback.

**Watch for this:** anything new that generates a command for Waybar must go
through `waybar._ccs()`, which appends the flag. `bashrc_block()` deliberately
does **not** — it is the terminal path and must use fzf. There is a test
(`test_bashrc_function_does_not_force_gui_mode`) pinning that, because this
exact mistake was made and caught during the session.

### fuzzel and empty stdin

`fuzzel --dmenu` exits immediately (rc=1) when its stdin is empty, so the
free-text nickname prompt could never be typed into — it just blinked. fuzzel
echoes unmatched input verbatim (per its man page), so `pickers.prompt()` now
feeds it one throwaway `HINT` row and treats that row as "no answer" if
selected.

### The oversized menu

219 history items filled a 1440 px screen top to bottom; a GtkMenu does not
scroll usefully. `paths.MENU_HIST_ITEMS = 15` now caps what the XML shows,
while `HIST_SLOTS = 300` still governs `menu-actions` and `history.tsv`. A
`> N more…` row appears when there is overflow and opens the fuzzel search.

Indices still align because shown row *i* maps to `hist-i` maps to `history.tsv`
line *i* — pinned by `test_shown_rows_map_to_the_matching_tsv_index`.

### Creating a project

The project picker is a fuzzel dmenu, and fuzzel echoes unmatched input back
verbatim — so typing a path that has no directory behind it used to resolve
fine and then die in kitty at `cd`, with the window closing instantly.

`pickers.confirm_create()` now catches that: the path in green (`--mesg` plus
`--message-color`, since fuzzel's default message grey reads as disabled text)
above an off-white `— press Enter to create project` row. `launch.run()` mkdirs
**only in `new` mode**, so a resume whose directory has since been deleted
still fails loudly rather than resurrecting an empty folder.

The ideal version of this is inline — the green path forming underneath the
input as you type. **fuzzel cannot do that**: dmenu rows are fixed once stdin
closes, there is no live-input hook and no markup mode (checked against fuzzel
1.14.1's full option list). A separate confirm window is the closest thing
without writing a custom layer-shell picker.

`--only-match` is load-bearing: without it fuzzel echoes typed text, and any
non-empty stdout here would read as consent to create a directory.

### Stale ●/○ markers

The radio dots and the ☐/☑ checkbox are baked into `menu.xml`, and Waybar
caches that file. `_refresh()` only fired the per-module `SIGRTMIN+n`, which
repaints the **label** and nothing else — so the colour really changed while
the dot stayed on whatever was selected when the bar last started.

`cli._refresh()` now rewrites the menu and calls `waybar.reload()`. That is
always a user-initiated click, so the reload flicker is acceptable; the 30 s
render tick still gates itself on `session_set_changed()` and does not flicker
on its own. The per-module signal call was dropped as redundant — `reload()`
subsumes it.

### Rename cancel was destructive

`pickers.prompt()` returns None for both "cancelled" and "typed nothing", so
Esc out of the rename dialog cleared the nickname it was backing out of.
`prompt_or_clear()` separates the three outcomes: text, None for an explicit
clear, and the `CANCEL` sentinel. fuzzel needs at least one row to stay open at
all, so that row is now the clear button rather than a throwaway hint.

A cleared nickname also shows **no text** on the bar now, not the email.
`label.render()` deliberately does not call `display_name()`; `display_name()`
keeps its email fallback for the menu title row and the account chooser, where
a blank row would be unpickable.

### Missing `modules-center`

Moving to the centre group exposed that the patcher only rewrites keys that
already exist, so a config without a centre group would silently swallow the
modules. `apply` now creates the group when absent and `strip` removes it again
when empty, keeping uninstall byte-for-byte either way.

---

## Things that will bite you

- **Never invoke `claude` by bare name from inside the code.** The `claude()`
  shell function would re-enter `ccs` forever. Always `paths.claude_bin()`.
  `CCAS_INNER=1` is exported into launched sessions as a second guard.
- **Never write to `~/.claude`.** Tests use `tmp_path` and the `CCAS_*` env
  overrides in `ccas/paths.py`. `test_relink_never_touches_mtimes_in_claude_home`
  is the sandbox canary.
- **Never delete.** Everything goes to `~/.claude_trash/`.
- `menu.xml` only regenerates on `ccs render`, which Waybar runs every 30 s.
  After changing menu-generation code, run `ccs render <slug>` to see it, or
  wait for the tick.
- Waybar caches `menu-file`; only `killall -SIGUSR2 waybar` re-reads it. A
  per-module `SIGRTMIN+n` refreshes the **label only** — this was measured.
- `~/.bashrc` changes only affect **new** shells.

## Useful commands

```bash
cd ~/ccas && python -m pytest          # 158 tests, ~0.6 s
./install.sh                           # idempotent; re-run after any code change
ccs list                               # accounts table
ccs render vsed                        # force menu.xml + history.tsv rebuild
./uninstall.sh                         # strip managed blocks, keep account data
./uninstall.sh --purge                 # also move ~/.cc-accounts to trash
```

Backups of the pre-CCAS originals:
`~/.config/waybar/config.jsonc.pre-ccas`, `~/.bashrc.pre-ccas`, plus
`*.ccas-orig` copies written by the installer.
