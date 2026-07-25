# CCAS — working notes for agents

Claude Code Account Switcher: one Waybar module per account, each account a
`CLAUDE_CONFIG_DIR` of its own. `HANDOFF.md` has the narrative history and every
bug found on the real system; this file is the rules that must not be broken.

## Hard invariants

**Never write to `~/.claude`.** CCAS only ever *reads* it; the symlinks live in
the account directory and point *at* it, never the reverse (`accounts.relink`).
That guarantee is the entire point of the design.

Check it **around your own command**, not against a constant:

```bash
before=$(stat -c %Y ~/.claude/.credentials.json); <the command>
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo BUG
find ~/.claude -maxdepth 1 -type l    # must stay empty
```

A hardcoded baseline (`1784954438`, quoted in older notes) is **not** a valid
canary. Claude Code running under the default account — no `CLAUDE_CONFIG_DIR`,
which includes the session you are probably in — refreshes that file's OAuth
token on its own; it moved to `1784983478` the moment the user's limits reset.
Prove the write was not ours before calling it a bug.

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

**Write and reload are one operation.** The tick rewrites `menu.xml` and
`history.tsv` only when it is also going to reload. Waybar is still showing the
menu it cached at the last reload, so a write without a reload leaves the
visible row and the `hist-i` action pointing at different sessions. Never move
one out from under the other.

**`ccs render` no longer forces a rebuild** — it writes only when it reloads, so
a code change that alters the XML will not reach the menus on disk. `ccs config`
(what `install.sh` runs) is the unconditional rebuild of every account's menu.

**Only use glyphs that survive the bar's font stack.** `style.css` here starts
`font-family: FontAwesome, "JetBrainsMono Nerd Font Mono", monospace`, and
FontAwesome wins for any codepoint it happens to cover. `☑` (U+2611) is in
FontAwesome, `☐` (U+2610) is not — so a checkbox pair rendered from two
different fonts at two different sizes and the checked state looked empty.
`menu.MARK_ON`/`MARK_OFF` (U+25CF/U+25CB) are the vetted pair; every stateful
row uses them. Check a new glyph before shipping it:

```bash
pango-view --font="FontAwesome, JetBrainsMono Nerd Font Mono, monospace 28" \
           -q -t '○ off  ● on  <new glyph>' -o /tmp/g.png
```

**`session_set_changed()` is set-based on purpose.** It compared the newest uuid
once, and with two live Claude sessions taking turns being newest, the bar
reloaded itself every few minutes for nothing. Session *order* changing is not a
reason to reload; a session *appearing* is. It must still return True when no
snapshot exists at all, or an account with no history never gets a menu file.

## The headless runner

`claude <args>` resolves an account through `cli._runner_slug()`; read it before
touching that path. Two rules there are load-bearing:

- **Never prompt when `sys.stdin.isatty()` is false.** Pipes, scripts and cron
  reach this code, and a prompt there blocks forever — the `is_gui` bug again.
- `NO_ACCOUNT_ARGS` is a **deny**-list. Adding to it is safe; switching it to an
  allow-list means an unknown flag silently runs under an account the user did
  not choose.

`headless` is a per-account bool kept exclusive by `registry.set_headless()`, so
the menu stays a pure function of the account dict. Changing it rewrites *every*
menu via `cli._refresh_all()`.

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
ccs config                       # force a rebuild of every menu.xml (render does not)
ccs headless [<slug>]            # show / toggle which account runs `claude -p`
./uninstall.sh [--purge]         # strip managed blocks; --purge also trashes ~/.cc-accounts
```
