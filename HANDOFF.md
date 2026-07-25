# CCAS — session handoff

**Written:** 2026-07-25, after the initial build session
**State:** built, installed, and working on the real system. 136 tests green.

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

Run the suite with `cd ~/ccas && python -m pytest` (136 passing, ~0.6 s).

---

## Verified live

- Placeholder module, add flow, OAuth login, both accounts isolated.
- Waybar modules render in `modules-center`: `✻ vo.se` peach, `✻ vsed` red.
- The menu opens with nested submenus: title row, New session, Resume last
  session, Resume from history ▸, Display as ▸, ☐ Hide icon, Color ▸, Manage ▸.
- Uninstall restores `config.jsonc` and `.bashrc` byte-for-byte (diffed).

## Not yet verified — start here

1. **Launching.** No session has actually been started yet. From the menu:
   `New session`, `Resume last session`, and a history row — each should open
   kitty in the session's own directory under the right account. This is the
   biggest untested area.
2. **Terminal front-end.** In a *new* shell (the `claude()` function is only in
   new shells): bare `claude` should show the account picker then the mode menu
   via **fzf**; `claude -p "say hi"` should run headless with no menu under the
   `default` account (`vo-se`); `ccs vsed claude auth status` should report the
   vsed account.
3. **Display controls.** Cycle all four display modes and both hide states.
   Confirm `icon only` + `Hide icon` fires the notification exactly once, that
   the invisible module is still clickable, and that unsetting and re-setting
   fires it again.
4. **`> ...` and `> N more…`** rows should open fuzzel over all 219 sessions.
5. **Rename / remove** from the Manage submenu.

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
cd ~/ccas && python -m pytest          # 136 tests, ~0.6 s
./install.sh                           # idempotent; re-run after any code change
ccs list                               # accounts table
ccs render vsed                        # force menu.xml + history.tsv rebuild
./uninstall.sh                         # strip managed blocks, keep account data
./uninstall.sh --purge                 # also move ~/.cc-accounts to trash
```

Backups of the pre-CCAS originals:
`~/.config/waybar/config.jsonc.pre-ccas`, `~/.bashrc.pre-ccas`, plus
`*.ccas-orig` copies written by the installer.
