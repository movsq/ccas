# CCAS — working notes for agents

Claude Code Account Switcher: one Waybar module per account, each account a
`CLAUDE_CONFIG_DIR` of its own. `HANDOFF.md` has the narrative history and every
bug found on the real system; this file is the rules that must not be broken.

## Hard invariants

**Never write to `~/.claude`.** CCAS reads it and symlinks *into* it. That
guarantee is the entire point of the design. `~/.claude/.credentials.json` mtime
has been `1784954438` since before the first commit — check it with
`stat -c %Y ~/.claude/.credentials.json` after anything that touches account
state, and treat a change as a bug, not a surprise.

**Never invoke `claude` by bare name from inside the code.** The install adds a
`claude()` shell function to `~/.bashrc`; a bare call re-enters it and loops
forever. Always `paths.claude_bin()`. `CCAS_INNER=1` is exported into launched
sessions as a second guard.

**Never delete.** Everything moves to `~/.claude_trash/` (global CLAUDE.md rule).
`install.sh` and `uninstall.sh` both obey this — do not "simplify" them to `rm`.

**Tests must never touch real state.** Every path goes through `ccas/paths.py`
and every one of them is env-overridable (`CCAS_HOME`, `CCAS_ACCOUNTS_ROOT`,
`CCAS_CLAUDE_JSON`, `CCAS_TRASH`, `CCAS_WAYBAR_CONFIG`, `CCAS_BASHRC`,
`CCAS_CCS_BIN`, `CCAS_CLAUDE_BIN`). `test_relink_never_touches_mtimes_in_claude_home`
is the canary. `CCAS_NO_RELOAD=1` suppresses signalling the live bar.

## Read the live state before you write it

This repo's tools mutate the user's real configuration. Read the registry, the
Waybar config, or the file you are about to replace *first* — a value you did
not capture is a value you cannot restore. A nickname was lost this way.

## Waybar facts, measured not assumed

Established by the Task 0 spike (`docs/superpowers/spike-waybar-menu.md`):

- Waybar **caches `menu-file` at startup**. Rewriting `menu.xml` changes nothing
  on screen until `killall -SIGUSR2 waybar`.
- A per-module `SIGRTMIN+n` repaints the **label only** — never the menu. Any
  state baked into the XML (the ●/○ marks, the ☐/☑ box, the title rows) needs a
  full reload.
- Nested submenus work, and `menu-actions` reaches nested items.
- A `GtkMenu` does not scroll usefully: 219 items filled a 1440 px screen.
  `paths.MENU_HIST_ITEMS` caps what is shown, `HIST_SLOTS` governs the actions
  and `history.tsv`. Shown row *i* → `hist-i` → tsv line *i*; keep them aligned.

So: a **user-initiated** change may reload (the flicker is expected). The 30 s
render tick must not — it gates itself on `menu.session_set_changed()`.

## GUI vs terminal

`pickers.is_gui()` is a **fallback only**. Waybar inherits stdin from the
compositor, which on a TTY session is a real terminal (`/dev/tty1`), so sniffing
`isatty()` misreports a bar click as a terminal invocation — and the terminal
path then blocks on `input()` against a console nobody can see.

Every Waybar-generated command carries `--gui`, appended by `waybar._ccs()`.
Anything new that generates a command for Waybar must go through it.
`bashrc_block()` deliberately does **not** — that path is a real terminal and
must use fzf. `test_bashrc_function_does_not_force_gui_mode` pins it, because
this exact mistake was made once already.

## fuzzel

- Exits instantly (rc=1) on **empty stdin**, whatever `--lines` says. A prompt
  with no rows can never be typed into; give it at least one row.
- **Echoes unmatched input verbatim**, which is how free-text prompts work here
  — and why `--only-match` is load-bearing on any prompt where stray text would
  be read as consent.
- No markup mode and no live-input hook: dmenu rows are fixed once stdin closes.
  Nothing can react to keystrokes. Colour comes from `--message-color` etc.,
  per-window, not per-row.
- Default width is 30 columns and it truncates silently. Compute a width.

## Working style

- TDD, inline (the user explicitly declined subagent-driven development):
  failing test → verify it fails → implement → verify it passes → commit.
- One commit per coherent change. **Never co-sign or co-author** (global rule).
- Verify on the real system rather than reasoning about it: `./install.sh`,
  `ccs render <slug>`, read the generated file. `grim` plus PIL cropping gives
  you a screenshot; Waybar is on `HDMI-A-1` (x 2560–4480), `DP-1` is x 0–2560.
- `~/.bashrc` changes only reach **new** shells.
- Update `HANDOFF.md` when behaviour changes — it is what the next session reads.

## Commands

```bash
cd ~/ccas && python -m pytest    # ~160 tests, under a second
./install.sh                     # idempotent; re-run after any code change
ccs list                         # accounts
ccs render <slug>                # force menu.xml + history.tsv rebuild
./uninstall.sh [--purge]         # strip managed blocks; --purge also trashes ~/.cc-accounts
```
