# CCAS — working notes for agents

Claude Code Account Switcher: one Waybar module per account, each account a
`CLAUDE_CONFIG_DIR` of its own. This file is the rules that must not be broken;
`docs/why.md` is the story behind them — every bug found on the real system.

## Start here

1. `python -m pytest` (~206 tests, under a second). They are the specification —
   every rule below is pinned by one, and the docstrings say which bug it was.
2. `ccs doctor` — is the live install healthy *before* you change anything?
3. `docs/why.md` when a rule here looks arbitrary — it has the bug that caused
   it, with the symptom and the cause.

Python 3.14, **stdlib only** at runtime, no build step. The entry point is
`bin/ccs` (`python -m ccas` does not work — there is no `__main__`), and
`install.sh` stages the package to `~/.local/share/ccas` and drops a launcher at
`~/.local/bin/ccs`.

| module | what it owns |
|---|---|
| `paths.py` | every filesystem location, each `CCAS_*`-overridable. Nothing else may hardcode a path. |
| `registry.py` | `accounts.json`: slugs, colours, display mode, `headless`, `default`. |
| `accounts.py` | the account directory: create, `relink` (the never-write-to-`~/.claude` guarantee), `rename`, trash, `env_for`. |
| `waybar.py` | the managed blocks in `config.jsonc` and `~/.bashrc`, plus reload/signal. |
| `menu.py` | `menu.xml` + `history.tsv`, and `session_set_changed()` which gates reloads. |
| `history.py` | scanning `~/.claude/projects` for sessions; row formatting. |
| `label.py` | the bar label and `display_name()`. |
| `pickers.py` | fzf vs fuzzel, and `is_gui()`. |
| `launch.py` | resolving a launch request into a cwd and argv. |
| `doctor.py` | the read-only audit. |
| `cli.py` | argument dispatch and the command bodies. |

One test file per module, same name. Add tests to the file that owns the
behaviour, not to `test_cli.py` by default.

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

**Never invoke `claude` by bare name from inside the code.** Always
`paths.claude_bin()`. CCAS no longer installs the `claude()` shell function that
made this fatal — `ccs -p …` replaced it, and `waybar.strip_bashrc()` takes the
old block out — but a shell opened before that install still has the function
loaded, and there a bare call re-enters it and loops forever. `CCAS_INNER=1` is
exported into launched sessions as a second guard.

**`claude` is the user's.** We shadowed it once; owning a binary the user did not
offer bought nothing that the passthrough does not. Any first argument beginning
with `-` (`ccs -p …`, `-c`, `-r`) goes to the real `claude` under the resolved
runner account, and `ccs -- mcp list` is the escape for claude's own subcommands,
which would otherwise read as `ccs` commands. Whatever is added to `main()`'s
dispatch, those two branches must stay ahead of the account-slug lookup and
behind the `--gui` strip.

**Never delete.** Everything moves to `~/.claude_trash/` (global CLAUDE.md rule).
`install.sh` and `uninstall.sh` both obey this — do not "simplify" them to `rm`.

**Tests must never touch real state.** Every path goes through `ccas/paths.py`
and every one of them is env-overridable (`CCAS_HOME`, `CCAS_ACCOUNTS_ROOT`,
`CCAS_CLAUDE_JSON`, `CCAS_TRASH`, `CCAS_WAYBAR_CONFIG`, `CCAS_BASHRC`,
`CCAS_CCS_BIN`, `CCAS_CLAUDE_BIN`). `test_relink_never_touches_mtimes_in_claude_home`
is the canary. `CCAS_NO_RELOAD=1` suppresses signalling the live bar.

**`ccs doctor` never writes.** It exists to police the invariants above, so a
repair inside it would mask the fault it is looking for — including a `relink`,
tempting as that is. It reports and says which command fixes it.

**Point Waybar at a menu only after writing it.** `cmd_add` writes `menu.xml`
before `waybar.apply()` adds the module, because `menu-file` is read on the
reload that follows. Anything new that creates a module must keep that order.

**A slug is permanent; a nickname is not.** The slug is the account directory
name and the Waybar module id. `cmd_add` names the directory after the email —
but only *after* login returns it, via `accounts.rename()` in the one window
where nothing references the account yet. Renaming a slug at any later point
means moving a directory a live session may hold open as `CLAUDE_CONFIG_DIR`.

## Read the live state before you write it

This repo's tools mutate the user's real configuration. Read the registry, the
Waybar config, or the file you are about to replace *first* — a value you did
not capture is a value you cannot restore. A nickname was lost this way.

## Waybar facts, measured not assumed

Established by the Task 0 spike (`docs/superpowers/spike-waybar-menu.md`);
`docs/waybar-setup.md` is the user-facing version, plus the styling CCAS does
*not* own.

- Waybar **caches `menu-file` at startup**. Rewriting `menu.xml` changes nothing
  on screen until `killall -SIGUSR2 waybar`.
- A per-module `SIGRTMIN+n` repaints the **label only** — never the menu. Any
  state baked into the XML (the ●/○ marks, the title rows) needs a
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

**The bar label is sized in pango markup, not CSS.** `label.ICON_SIZE`,
`ICON_RISE` and `TEXT_SIZE` — a CSS `font-size` scales glyph and nickname
together, which is never what is wanted. `ICON_RISE` is the non-obvious one:
both runs share a baseline and `✻` carries more ink above it than a digit, so an
enlarged glyph rides high beside its own label. Changing any of the three means
re-checking the pair, and **measure it — do not eyeball it**. Render the exact
markup with `pango-view --markup` and compare the two runs' ink extents; that
works with the bar covered or off-screen, which `grim` does not. Recipe and
constants table: `docs/waybar-setup.md`.

**CCAS does not own `style.css`.** It writes the managed block in
`config.jsonc` and nothing else. Spacing, hover and press feedback are hand-set
per slug (`#custom-cc-<slug>`; GTK CSS has no prefix matching), documented in
`docs/waybar-setup.md`. Back the file up before touching it — it is the user's.

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

`ccs <claude args>` resolves an account through `cli._runner_slug()`; read it
before touching that path. Two rules there are load-bearing:

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
The `ccs -p` passthrough deliberately does **not** — that path is a real terminal
and must use fzf. Two tests pin the pair
(`test_generated_commands_all_force_gui_mode`,
`test_passthrough_does_not_force_gui_mode`), because this exact mistake was made
once already, back when the bashrc function was the terminal path.

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
- **`ccs` runs the installed copy, not this repo.** `install.sh` stages the
  package into `~/.local/share/ccas`, so any test that goes through the `ccs`
  command exercises the code as of the last install. Editing a module and then
  running `ccs …` silently tests the *old* behaviour — which happened here, and
  a working fix was nearly reported as broken. **Re-run `./install.sh` before
  every live check**, and if a live result contradicts a passing test, suspect
  a stale install before suspecting the test.
- `git remote origin` is `github.com/movsq/ccas` (private). Push when the user
  asks; the user set it up so work is not only on this disk.
- `~/.bashrc` changes only reach **new** shells — which is why the terminal
  entry point is `ccs`, not a shell function.
- Add a section to `docs/why.md` when you fix a bug found on the real system, or
  deviate from what a plan said. Not for routine changes, and never for status —
  it holds no test counts and no "as of today", which is exactly why it survives
  without maintenance.

### Exercising a flow that would touch real state

`ccs add` logs in, writes credentials and rewrites the bar — not something to
try against the live install. Point every path at a scratch directory and stub
the binary instead:

```bash
export CCAS_HOME=$SB/home CCAS_ACCOUNTS_ROOT=$SB/accts CCAS_CLAUDE_JSON=$SB/claude.json \
       CCAS_WAYBAR_CONFIG=$SB/config.jsonc CCAS_BASHRC=$SB/bashrc CCAS_TRASH=$SB/trash \
       CCAS_CLAUDE_BIN=$SB/fakeclaude CCAS_NO_RELOAD=1
script -qec "ccs add" /dev/null < /dev/null      # a pty, on purpose — see below
```

The fake `claude` needs two cases: `auth login` writes
`$CLAUDE_CONFIG_DIR/.credentials.json`, `auth status` prints
`{"loggedIn":true,"email":"…"}`.

`script` is load-bearing. `pickers.is_gui()` is `not sys.stdin.isatty()`, so a
pipe or `</dev/null` takes the **fuzzel** path and the run dies with no prompt.
A pty makes it the terminal path. Then audit the result with `ccs doctor` — that
is how the missing-`menu.xml` bug in `cmd_add` was found.

## Commands

```bash
cd ~/ccas && python -m pytest    # ~206 tests, under a second
./install.sh                     # idempotent; re-run after any code change
ccs list                         # accounts
ccs doctor                       # audit the four places that drift; rc 1 if any failed
ccs config                       # force a rebuild of every menu.xml (render does not)
ccs headless [<slug>]            # show / toggle which account runs `ccs -p`
ccs -p "…" / ccs -c / ccs -r     # real claude under the runner account
ccs -- mcp list                  # claude's own subcommands need the --
./uninstall.sh [--purge]         # strip managed blocks; --purge also trashes ~/.cc-accounts
```

## Unverified — claims made but never measured

Written 2026-07-25. These shipped on reasoning, not evidence. Check them before
building on them; delete the line once you have.

- **The bar styling was never seen working.** `#custom-cc-*` spacing, `:hover`
  and `:active` went into `~/.config/waybar/style.css` with zero live
  confirmation — a window was covering the bar on `DP-1` and `grim` came back
  with no teal glyph in it. Nothing proves any of the three renders.
- **`:active` is the shakiest of them.** Waybar's custom module is
  EventBox-backed and GTK's active state is a button concept, so the press
  effect may simply never fire. `:hover` is documented; the press is a guess.
- **`ICON_SIZE = "150%"` was aligned offline only.** `pango-view` says the glyph
  and the text ink the same rows at `ICON_RISE = "-800"`, and pango-view
  rasterises at its own DPI — proportional to the bar, not identical to it. The
  earlier `x-large` value *was* measured on the real bar; this one was not.
- **Waybar is running from a hand-started `setsid`** (was PID 3250290), because
  a broken intermediate `label.py` took the bar down mid-session. A relogin puts
  it back under whatever normally supervises it. If the bar behaves oddly and
  the process has no supervisor, that is why.

Do not verify any of this by asking the user to click things — automate it.
`grim` plus ink-extent maths, `pango-view --markup` for glyph metrics with no
bar involved, `swaymsg -t get_tree` to find out what is covering the bar.
