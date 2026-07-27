# CCAS — working notes for agents

Claude Code Account Switcher: one Waybar module per account, each account a
`CLAUDE_CONFIG_DIR` of its own. This file is the rules that must not be broken;
`docs/why.md` is the story behind them — every bug found on the real system.

## Start here

1. `python -m pytest` (~330 tests, under a second). They are the specification —
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
| `registry.py` | `accounts.json`: slugs, colours (hex), `headless`, `default`. No migrations — see below. |
| `accounts.py` | the account directory: create, `relink` (the never-write-to-`~/.claude` guarantee), `rename`, trash, `env_for`. |
| `waybar.py` | the managed blocks in `config.jsonc` and `style.css`, plus reload/signal. |
| `history.py` | scanning `~/.claude/projects` for sessions; row formatting. |
| `label.py` | the bar label, `display_name()`, and the `MARK_ON`/`MARK_OFF` pair. |
| `format.py` | the format string — the only way a label is built: the token table, `tokens_in`, `unknown_tokens`, `render()`, `random_color()`, and the hue/saturation maths. Pure — no I/O and no GTK, because `ccs statusline` reaches it. |
| `usage.py` | the per-account usage reading: recording it, all three source shapes, the three states, and how each is said. |
| `poll.py` | the session-free usage fetch: the credential read, the freshness gate, the request. Its fetcher is injected, so no test opens a socket. |
| `pickers.py` | the terminal front-ends (fzf, `input()`), and `is_gui()`. |
| `panel.py` | the GTK panel's state: `build_state`, `filter_state`, the open-panel lock, which output. No GTK. |
| `panel_ui.py` | the panel's widget tree. The only module that touches GTK, and it decides nothing. |
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
made this fatal — `ccs -p …` replaced it — but a shell opened before it went
away still has the function loaded, and there a bare call re-enters it and
loops forever. `CCAS_INNER=1` is
exported into launched sessions as a second guard.

**`claude` is the user's.** We shadowed it once; owning a binary the user did not
offer bought nothing that the passthrough does not. Any first argument beginning
with `-` (`ccs -p …`, `-c`, `-r`) goes to the real `claude` under the resolved
runner account, and `ccs -- mcp list` is the escape for claude's own subcommands,
which would otherwise read as `ccs` commands. Whatever is added to `main()`'s
dispatch, those two branches must stay ahead of the account-slug lookup and
behind the `--gui` strip.

**Never delete.** Everything moves to `.claude_trash/` in the CCAS checkout
(global CLAUDE.md rule — the path is repo-relative, as that rule states; an
earlier version of this code expanded it to `$HOME` on its own, which was not
the intent). `install.sh` and `uninstall.sh` derive it from `$SRC`;
`paths.trash_dir()` falls back to `paths.REPO_ROOT`, a literal, because the
installed package runs out of `~/.local/share/ccas` and cannot find its
checkout. Override `CCAS_TRASH` to install from anywhere else. Do not
"simplify" any of this to `rm`.

**Tests must never touch real state.** Every path goes through `ccas/paths.py`
and every one of them is env-overridable (`CCAS_HOME`, `CCAS_ACCOUNTS_ROOT`,
`CCAS_CLAUDE_JSON`, `CCAS_TRASH`, `CCAS_WAYBAR_CONFIG`, `CCAS_WAYBAR_STYLE`,
`CCAS_CCS_BIN`, `CCAS_CLAUDE_BIN`, `CCAS_MENU_CSS`, `CCAS_PANEL_OUTPUT`,
`CCAS_PANEL_LOCK`). `test_relink_never_touches_mtimes_in_claude_home`
is the canary. `CCAS_NO_RELOAD=1` suppresses signalling the live bar, and
`CCAS_SYSTEMD_DIR` plus `CCAS_SKIP_SYSTEMD=1` keep `install.sh` from writing and
enabling a real systemd unit — both are needed, and the shell reads them
directly rather than through `paths.py`.

**Nothing migrates.** One user, one installation, and the user re-adds their
accounts rather than being carried across a rename. `registry.load()` defaults
the keys every read path needs and does nothing else; a stored value that is no
longer valid — a `format_colors` entry of `auto`, `dim` or `account` — reads as
absent and renders `format.DEFAULT_COLOR`. When something is retired, delete it;
do not grow a read path that understands both. A stored *format string* naming a
retired token is not rewritten either: `%icon` prints as its own four characters
until someone runs `ccs format <slug> …`, which is visible and self-explaining
where a silent rewrite is neither.

**A token's colour is a hex, or nothing.** Nothing means `format.DEFAULT_COLOR`,
white, and it means that for every token. `auto` made the answer depend on which
token asked — the usage ramp for the windowed ones, the account colour for the
glyph. `account` made it depend on whether *any* token asked: an account whose
tokens all held literal hexes had a live, previewed, entirely inert widget-colour
control, and the user spent ten minutes finding that out. There is no usage ramp
in the label; `usage.color()` still ramps inside `usage.bar()` and the panel's
bars, which is where pressure is shown.

**The glyph is the widget's, not the format's.** `%icon` is not a token. The ✻ is
drawn by `format.render()` ahead of the format string, in the account's colour,
and `hide_icon` removes it outright — no `alpha='1'` spacer, which would reserve
the glyph's width in exactly the labels that asked not to have one. One colour
with one name: it paints the bar's glyph and the panel's account dot, and nothing
can disagree with it.

**`ccs doctor` never writes.** It exists to police the invariants above, so a
repair inside it would mask the fault it is looking for — including a `relink`,
tempting as that is. It reports and says which command fixes it.

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

CCAS no longer uses `menu-file` at all: the bar's click runs `ccs --gui <slug>`,
which opens the GTK panel, built fresh per click. The facts above are why — a
menu you cannot invalidate has to be invalidated by rebuilding the bar, and that
is what blinked the bar every time a new session appeared. The two steps out of
`menu-file` are `docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md`
and `docs/superpowers/specs/2026-07-26-gtk4-panel-design.md`.

So the only thing that still reloads is a change to the **set of modules**
(`cmd_add`, `cmd_rm`), because that rewrites `config.jsonc`, which Waybar reads
at startup. Every other setting is a `waybar.signal(n)`; the 30 s render tick
prints a label and nothing else.

**The bar label is sized in pango markup, not CSS.** `label.ICON_SIZE`,
`ICON_RISE` and `TEXT_SIZE` — a CSS `font-size` scales glyph and nickname
together, which is never what is wanted. `ICON_RISE` is the non-obvious one:
both runs share a baseline and `✻` carries more ink above it than a digit, so an
enlarged glyph rides high beside its own label. Changing any of the three means
re-checking the pair, and **measure it — do not eyeball it**. Render the exact
markup with `pango-view --markup` and compare the two runs' ink extents; that
works with the bar covered or off-screen, which `grim` does not. Recipe and
constants table: `docs/waybar-setup.md`.

**CCAS owns one block in `style.css` and nothing else.** The widgets' spacing
and hover, appended at the end of the file by `waybar.apply_style()` — last, so
that equal-specificity rules above it lose. Everything else in that file is the
user's; back it up before touching it, and `.ccas-orig` holds what was there
before CCAS first wrote. The block is generated rather than hand-kept because
GTK CSS has no prefix matching: `#custom-cc-*` is not a selector, so every slug
is spelled out, and a hand-kept list goes stale the first time an account is
renamed — the widget still draws and still clicks, it just stops answering the
pointer. `ccs doctor` reports that. Details in `docs/waybar-setup.md`.

**Only use glyphs that survive the bar's font stack.** `style.css` here starts
`font-family: FontAwesome, "JetBrainsMono Nerd Font Mono", monospace`, and
FontAwesome wins for any codepoint it happens to cover. `☑` (U+2611) is in
FontAwesome, `☐` (U+2610) is not — so a checkbox pair rendered from two
different fonts at two different sizes and the checked state looked empty.
`label.MARK_ON`/`MARK_OFF` (U+25CF/U+25CB) are the vetted pair, and the rule
applies to **anything Waybar draws** — the bar label and the fzf rows, which
inherit the terminal's font. It does *not* apply to the panel: that is a
separate process with its own stylesheet and no FontAwesome in sight, which is
why its toggles are real `GtkCheckButton`s rather than a glyph pair. Check a new
glyph before shipping it anywhere the bar can see it:

```bash
pango-view --font="FontAwesome, JetBrainsMono Nerd Font Mono, monospace 28" \
           -q -t '○ off  ● on  <new glyph>' -o /tmp/g.png
```

## The headless runner

`ccs <claude args>` resolves an account through `cli._runner_slug()`; read it
before touching that path. Two rules there are load-bearing:

- **Never prompt when `sys.stdin.isatty()` is false.** Pipes, scripts and cron
  reach this code, and a prompt there blocks forever — the `is_gui` bug again.
- `NO_ACCOUNT_ARGS` is a **deny**-list. Adding to it is safe; switching it to an
  allow-list means an unknown flag silently runs under an account the user did
  not choose.

`headless` is a per-account bool kept exclusive by `registry.set_headless()`, so
the picker stays a pure function of the account dict. It is not in the label, so
changing it touches the bar not at all — `cli._refresh_all()` is now just a save.

## Usage limits

`docs/usage-limits-research.md` is the measurement; **do not re-run its probes**,
one costs real quota. Four rules hold the feature up:

- **CCAS never wires the hook.** Every account's `settings.json` is a symlink to
  `~/.claude/settings.json`, so writing it breaks the invariant. `ccs doctor`
  reports the exact line and the user adds it.
- **`ccs statusline` is a wrapper, not a statusline.** The user already had one.
  Same bytes to the delegate, its stdout verbatim, its exit code ours, no
  timeout — and every recording failure swallowed, because a statusline that
  raises is visible in every prompt of every session.
- **Only an account records.** `usage.account_slug()` returns None unless
  `CLAUDE_CONFIG_DIR` resolves to a directory directly under `accounts_root()`,
  so the default account — the one an agent is probably in — writes nothing.
- **Write only on a change**, and let `waybar.signal()` ride on the write. The
  hook fires every few hundred milliseconds; the numbers move every few minutes.

The display is the **reset time, not the percentage**: `resets_at` is an absolute
anchor, so past means the window rolled over and future makes the recorded
percentage a lower bound (`≥`). That is also why there is no staleness cutoff
anywhere — for the 5-hour window, stale and rolled-over are the same test.

**The hook serves the account in use; the timer serves the idle one.** The hook
only fires inside a session, so the moment the last one closes that account's
numbers freeze — measured 2026-07-27, one account's reading was six seconds old
while the other's was five hours. So `poll.py` asks
`GET /api/oauth/usage` — the endpoint Claude Code itself asks — and a systemd
user timer runs `ccs poll` every five minutes. Four rules there:

- **The cadence lives in the unit and nowhere else.** No CCAS setting to drift
  out of sync with systemd; `systemctl --user edit ccas-poll.timer` retimes it.
  `install.sh` writes both units and trashes any existing one first.
- **Never write `.credentials.json`, and never refresh a token.** Read the
  access token, use it, treat expiry as "no fetch" — never as "renew". A
  rotation risks that account's login and can invalidate the token a live
  session is holding. The horizon that buys is about eight hours, after which
  the account goes quiet until it is next used. That is a degradation, not a
  hole: an account idle that long has rolled its 5-hour window over, which
  `usage.state()` reports from the timestamp alone.
- **`due()` skips an account the hook is already keeping fresh**, so a live
  session is never polled for and the request is spent on the idle account.
- **The signal still rides on the write.** `record_reading` is the half of
  `record` that takes an already-normalised reading, so the poll reaches the
  same write-only-on-change rule the hook obeys — which also means an idle
  account whose numbers have not moved is correctly *not* repainted.

A poller that quietly died looks exactly like the staleness it exists to remove,
so `ccs doctor` reports the timer's state and each account's reading age and
token horizon. Tests must set `CCAS_SYSTEMD_DIR` and `CCAS_SKIP_SYSTEMD=1`
around anything that runs `install.sh`, and stub `poll.timer_state` — otherwise
they enable a real unit and shell out to the user's real systemctl.

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
`test_passthrough_does_not_open_the_panel`), because this exact mistake was made
once already, back when the bashrc function was the terminal path.

**The two doors differ in their toolkit, not in their verbs.** `cmd_mode_menu`
sends `gui` to `cmd_panel` and keeps the fzf list for a terminal, because a TTY
and an ssh session cannot run GTK. They used to differ in their *launch verbs*
as well — the bar click had no meaningful cwd, so directory-free labels were the
only honest ones. The panel's project pane supplies a real directory, so that
rule is retired; `docs/why.md` has both halves of it.

**The three manage verbs open a terminal.** Rename wants free text, remove a
confirmation, add an interactive login, and the panel closes the moment a button
is clicked — so `cli._in_terminal()` spawns kitty running the same `ccs manage …`
the user could have typed. A bar click has no stdin, and an `input()` there
blocks forever against a console nobody can see.

## The panel

`ccs --gui <slug>` opens one GTK4 layer surface and returns one
`panel.Action`; `cli.dispatch_panel` turns that into a command that already
existed. Four things about it are load-bearing and were each a bug first:

- **`gtk4-layer-shell` must be `CDLL`-loaded RTLD_GLOBAL before `import gi`**,
  or every surface silently degrades to an ordinary window.
- **`gi` is imported inside `show()`**, never at module scope: `ccs statusline`
  runs in every prompt of every session and must not need PyGObject. Two tests
  pin the boundary.
- **The surface covers its whole output** (all four edges, exclusive zone 0) so
  that a click beside the panel is delivered to CCAS and goes no further —
  Wayland gives a press to exactly one surface, so dismissing the panel cannot
  also pause a video. Exclusive zone 0 keeps Waybar reachable, which is what
  makes the widget a toggle.
- **Keyboard mode is EXCLUSIVE**, so Escape closes it whether or not it was ever
  clicked, and the Escape handler is on the window in the CAPTURE phase because
  `GtkSearchEntry` eats Escape to clear itself.

- **`Gio.ApplicationFlags.NON_UNIQUE`**, or a second panel process is not a
  process: an `application_id` makes `Gtk.Application` single-instance, so while
  the panel being replaced still holds the id, `app.run()` forwards `activate`
  to *it* and returns without a main loop. The click closed a panel and opened
  nothing. The flag is on `Gio` in GTK4 — `Gtk.ApplicationFlags` raises
  `AttributeError`, outside the import guard, and the panel silently never
  appears.

- **A per-widget `CssProvider` must go on above `PRIORITY_USER`.** `_load_css()`
  installs the user's `menu.css` at `STYLE_PROVIDER_PRIORITY_USER`, so a widget
  provider added at `PRIORITY_APPLICATION` is outranked by any rule in that file
  and paints nothing — silently, since a losing rule is not an error. The colour
  editor's preview swatch is the one widget that needs its own provider (a
  colour mid-drag is in neither set `_load_tints()` built classes from, so
  `_tint()` names a class that does not exist), and it uses `PRIORITY_USER + 1`.

- **Never put a `GestureClick` on a `Gtk.Scale`.** It claims the event sequence
  and denies the scale's own drag gesture: the press registers and the knob then
  never tracks the pointer. `EventControllerLegacy` is not the escape — its
  handler is passed a `None` event here. `Gtk.Scale` has no "released" signal in
  GTK4 at all, so anything wanting commit-on-release uses the `SETTLE_MS` timer
  rearmed on `value-changed` instead. That still honours write-only-on-a-change
  with `waybar.signal()` riding on the write, and covers the keyboard for free.

The open panel writes its pid, slug **and connector** to `paths.panel_lock()`.
Waybar draws every module on every bar, so one account has one widget per
monitor: *the same widget* is the slug **and** the output, and comparing slugs
alone made the copy on the other bar read as a second click — the panel closed
and nothing opened. `SIGTERM` does not run a finally block, so the sender clears
the lock, and a lock naming a dead pid reads as nothing being open.

`cmd_panel` therefore closes the running panel **inside the gate**, not before
`panel_ui.show()`: which monitor the click came from is not known until the
pointer probe answers, inside the panel process. The gate is called once with
the settled connector, closes what was open, and returns False when that was
this widget's own panel. A chip click reopens through the same gate — hence the
`first` flag, or the panel would send itself `SIGTERM` — and reuses the
connector rather than probing again.

`PROBE_MS` is **1500, and it is a mouse-hold budget, not a repaint one.** Waybar
spawns `on-click` on button *press*, sway holds an implicit pointer grab until
the button comes up, and a surface mapping under the cursor during a grab is
told nothing — so a click held longer than the timeout probed nothing and opened
on the focused *window's* monitor. Nothing normal pays it: `enter` still answers
in about 5 ms.

Which output it opens on: `CCAS_PANEL_OUTPUT` wins, then the **pointer probe**,
then `panel.current_output()`, then the compositor. The probe is the real answer
and `_pointer_output()` is worth reading before touching it: sway's IPC has no
cursor position, and its `focused` output is the one holding the focused
*window*, which under the user's `focus_follows_mouse no` is reliably not where
they clicked. So a transparent layer surface is mapped on every monitor and the
one that receives `wl_pointer.enter` names the output — the same trick fuzzel
uses. **A GTK4 window that paints nothing gets no input region**, so both the
probe and the scrim carry a 0.01-alpha background rather than `transparent`;
that is not cosmetic, it is what makes them receive a pointer at all.

`assets/menu.css` is the stylesheet. `install.sh` copies it to
`~/.config/ccas/menu.css` once and never overwrites it: it is the user's the
moment it exists, the same stance CCAS takes toward `style.css`.

## Working style

- TDD: failing test → verify it fails → implement → verify it passes → commit.
- One commit per coherent change. **Never co-sign or co-author** (global rule).
- Verify on the real system rather than reasoning about it: `./install.sh`,
  `ccs render <slug>`, read `~/.config/waybar/config.jsonc`. `grim` plus PIL
  cropping gives you a screenshot; Waybar is on `HDMI-A-1` (x 2560–4480),
  `DP-1` is x 0–2560.
- **A synthetic pointer only works on the output the user is already using.**
  `swaymsg cursor` moves the cursor, but sway does not recompute pointer focus
  on an idle output, so a layer surface there receives nothing and the test
  reads as a broken feature. Half a day went into that once — `docs/why.md` has
  it. A uinput keyboard has no such limit; it is a real device.
  Sharpened 2026-07-27: a **press** does reach an idle output's layer surface —
  clicking a slider's trough at a new position moves it, which is enough to
  verify a commit path end to end — but **motion delivered while the button is
  held does not**, so a drag there proves nothing. Design the check around a
  click, not a drag.
- **Never map a test window onto the output the user is working on.** Decide
  where a surface will land *before* spawning it, pin it to an idle output, and
  kill it once the capture is taken. Which output that is changes; ask or look
  rather than assuming, and if the answer is derivable without a window at all,
  derive it.
- **Open the panel with `CCAS_PANEL_OUTPUT=<connector>` when you drive it
  yourself.** It skips the pointer probe, which is the previous rule's victim:
  the probe waits for `wl_pointer.enter`, an idle output never sends one, and
  the panel then opens nowhere at all while its process sits there presenting
  frames. `(CCAS_PANEL_OUTPUT=HDMI-A-1 setsid ccs --gui <slug> >log 2>&1 &)`,
  sleep 3, then `grim`.
- **`pkill -f` matches the shell you typed it in.** `pkill -f "ccs --gui"` kills
  the bash running it, because that string is in its own `/proc/…/cmdline` — so
  the panel is never launched and reads as a panel that will not open. It cost
  half a dozen rounds of that once. Kill a pid from `pgrep`, or pick a pattern
  the command line cannot contain.
- **Diff the widgets, never the whole bar.** `grim -g "2560,0 1920x24"` and a
  pixel compare answers CHANGED every single time, because the clock and the
  stopwatch beside it tick on their own — so it says "the label repainted" just
  as loudly when nothing repainted at all. It nearly passed a fix that had not
  been shown to work. The two account widgets sit at global x 3420–3640 with the
  bar 22 px tall (`grim -g "3420,0 220x22"`), which holds nothing that changes
  by itself; re-derive it from `ccs render` output widths if a module is added.
  Sampling a background colour at a fixed pixel is better still — the hover
  check is `(53,53,53)` unhovered against `(73,73,73)` under the pointer, and
  neither number moves on its own.
- **Clicking something yourself: `swaymsg seat - cursor move`, never `cursor
  set`.** `set` teleports the pointer — the cursor lands on the target and
  `grim -c` proves it, but no motion event reaches a layer surface, so it keeps
  highlighting the row it started on and the click that follows lands on the
  wrong one. A relative `move` of a pixel or two delivers real motion; then
  `cursor press button1` / `release button1` selects. Beware `move -1 0`: the
  leading `-` is read as a swaymsg option. Locate the window by its **selection
  bar** — the longest horizontal run of the highlight colour — rather than by
  diffing before/after screenshots, which anything else moving on screen ruins.
  Aim at a row that is *not* the highlighted first one, or a stray keypress is
  indistinguishable from a successful click.
- **A change to `assets/menu.css` does not reach the live panel.** `install.sh`
  copies it to `~/.config/ccas/menu.css` once and never again — it is the
  user's after that. Copy it across by hand to test a stylesheet fix, and back
  up the live one first in case they have edited it.
- **`ccs` runs the installed copy, not this repo.** `install.sh` stages the
  package into `~/.local/share/ccas`, so any test that goes through the `ccs`
  command exercises the code as of the last install. Editing a module and then
  running `ccs …` silently tests the *old* behaviour — which happened here, and
  a working fix was nearly reported as broken. **Re-run `./install.sh` before
  every live check**, and if a live result contradicts a passing test, suspect
  a stale install before suspecting the test.
- `git remote origin` is `github.com/movsq/ccas` (private). Push when the user
  asks; the user set it up so work is not only on this disk.
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
       CCAS_WAYBAR_CONFIG=$SB/config.jsonc CCAS_WAYBAR_STYLE=$SB/style.css CCAS_TRASH=$SB/trash \
       CCAS_CLAUDE_BIN=$SB/fakeclaude CCAS_NO_RELOAD=1
script -qec "ccs add" /dev/null < /dev/null      # a pty, on purpose — see below
```

The fake `claude` needs two cases: `auth login` writes
`$CLAUDE_CONFIG_DIR/.credentials.json`, `auth status` prints
`{"loggedIn":true,"email":"…"}`.

`script` is load-bearing. `pickers.is_gui()` is `not sys.stdin.isatty()`, so a
pipe or `</dev/null` is read as a bar click and takes the **GTK** path, where
there is nothing to type into. A pty makes it the terminal path. Then audit the result with `ccs doctor` — that
is how the missing-`menu.xml` bug in `cmd_add` was found, back when `cmd_add`
still generated files.

## Commands

```bash
cd ~/ccas && python -m pytest    # ~330 tests, under a second
./install.sh                     # idempotent; re-run after any code change
ccs                              # terminal: pick account → mode (fzf)
ccs --gui <slug>                 # the bar's click: the GTK panel. A second one closes it.
ccs list                         # accounts
ccs doctor                       # audit the four places that drift; rc 1 if any failed
ccs config                       # rewrite both managed blocks (config.jsonc, style.css) and reload
ccs headless [<slug>]            # show / toggle which account runs `ccs -p`
ccs dangerous [<slug>]           # show / toggle --dangerously-skip-permissions per account
ccs color <slug> <#rrggbb|n>     # the widget's colour; a palette index is shorthand
ccs format <slug> ['<fmt>']      # show / set the format string — the whole label
ccs format --tokens              # every token, with what it renders
ccs format <slug> --color %5h '#89b4fa'  # one token's colour: #rrggbb, or - to clear
ccs usage [<slug>]               # both quota windows, their age and their source
ccs statusline [<delegate> …]    # the recording hook; wired by hand in ~/.claude/settings.json
ccs poll [<slug>] [--force]      # fetch usage with no session running; what the systemd timer runs
ccs -p "…" / ccs -c / ccs -r     # real claude under the runner account
ccs -- mcp list                  # claude's own subcommands need the --
./uninstall.sh [--purge]         # strip managed blocks; --purge also trashes ~/.cc-accounts
```

## The bar's pointer states, measured

Settled 2026-07-26 by driving the pointer from `swaymsg` and sampling pixels;
nothing here is a guess any more.

- **`:hover` fires.** The module's background goes `#353535` → `(73,73,73)`
  under the pointer, which is `rgba(255,255,255,0.10)` composited over the bar
  exactly as `style.css` asks for it.
- **`:active` does not fire.** Held under `cursor press button1`, the background
  stays at the hover value instead of reaching the `(97,97,97)` that 0.22 would
  give. The suspected reason was the right one: Waybar's custom module is
  EventBox-backed and GTK's active state is a button concept. The rule in
  `style.css` is inert — harmless, but do not spend time tuning it.
- **The click reaches the picker.** Pressing the module spawns
  the panel, so `on-click` works end to end on the live bar, not just in the
  generated config.

Never verify this sort of thing by asking the user to click things — automate
it. `grim` plus ink-extent maths, `pango-view --markup` for glyph metrics with
no bar involved, `swaymsg -t get_tree` to find out what is covering the bar, and
the `cursor move` recipe under Working style to click something yourself.

Waybar is still running from a hand-started `setsid`, and a relogin puts it back
under whatever normally supervises it. That much was already known; what it
*cost* was found on 2026-07-25 and is no longer a guess. The bar had been
restarted from inside an agent session, so it carried
`CLAUDE_CODE_CHILD_SESSION=1` and silently disabled transcript saving for every
session launched off it — while `ccs` typed in a terminal was fine, which is
what made it look random. **Never restart a long-lived process from inside an
agent shell without scrubbing the environment**; `docs/why.md` has the `/proc`
walk that identifies the carrier and the scrubbed restart command.
