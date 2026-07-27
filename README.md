# CCAS — Claude Code Account Switcher

Switch between multiple Claude Code accounts from Waybar and from the terminal,
changing authentication only. Settings, plugins, themes and session history stay
shared, because they are shared by reference rather than copied.

Each account owns a coloured `✻` in Waybar. Clicking it opens the CCAS panel: a
single window with both quota windows, a project list beside that project's
sessions, a search that narrows both, every per-account toggle visible without
opening anything, and the launch verbs. Clicking the same widget again closes
it, as do Escape and a click anywhere outside it.

## The isolation model

Each account is a `CLAUDE_CONFIG_DIR` at `~/.cc-accounts/<slug>/` holding just
two real files — `.credentials.json` and `.claude.json` — with every other entry
a symlink back into `~/.claude`. Launching is
`CLAUDE_CONFIG_DIR=~/.cc-accounts/<slug> claude`, so two accounts can run at
once and a bug in CCAS cannot corrupt your real configuration.

**CCAS never writes to `~/.claude`.** It only reads it and symlinks into it.
That is the guarantee the whole design rests on, and it is asserted in the test
suite (`test_relink_never_touches_mtimes_in_claude_home`) as well as live.

`ccs relink` runs on every launch and re-shares anything a Claude Code update
added to `~/.claude`, so new top-level entries never get stranded inside a
single account.

## Install

```bash
./install.sh          # idempotent; safe to re-run after a system update
```

This installs `ccs` to `~/.local/bin/`, stages the package under
`~/.local/share/ccas/`, writes a fenced managed block into
`~/.config/waybar/config.jsonc`, and copies the panel's stylesheet to
`~/.config/ccas/menu.css` if you do not already have one. Only the region
between the sentinels in `config.jsonc` is ever touched, and a `*.ccas-orig`
copy is written alongside it before the first modification.

With no accounts configured, a dim `✻` appears in Waybar; click it to add your
first account.

The panel needs PyGObject, GTK 4 and gtk4-layer-shell
(`sudo pacman -S python-gobject gtk4 gtk4-layer-shell`). `ccs doctor` says so if
they are missing, and the terminal door works without them. Everything CCAS runs
outside the panel is stdlib-only.

```bash
./uninstall.sh            # strips the managed blocks, leaves account data
./uninstall.sh --purge    # also moves ~/.cc-accounts to the repo's .claude_trash
```

Nothing is ever deleted. Removal always means a move to `.claude_trash/` in the CCAS checkout.

## Two doors

**From the bar**, `ccs --gui <slug>` opens the GTK panel — one layer surface,
built fresh per click, covering its output so that a click beside it dismisses
it without reaching whatever is underneath. It opens on the monitor your pointer
is on; `CCAS_PANEL_OUTPUT=HDMI-A-1` pins it if you would rather it did not move.

**From a terminal**, `ccs <slug>` opens the same choices as an fzf list, because
a TTY and an ssh session cannot run GTK. The two doors differ in their toolkit
and in nothing else.

Rename, remove and add open a terminal of their own from either door: they want
free text, a confirmation and an interactive login respectively, and a bar click
has no stdin to ask on.

## Commands

| Command | Behaviour |
|---|---|
| `ccs` | Terminal: pick an account, then what to do with it. |
| `ccs <slug>` | That account's menu, in the terminal. |
| `ccs --gui <slug>` | The panel. What the bar runs; a second one closes it. |
| `ccs list` | Table of accounts: index, slug, email, colour, display mode. |
| `ccs doctor` | Read-only audit of everything that can drift. Never writes. rc 1 if anything failed. |
| `ccs add` | Create the directory, relink, seed config, run login, record the email. |
| `ccs rm <slug>` | Move the account directory to `.claude_trash/`, drop it from the registry. |
| `ccs manage <action> [<slug>]` | `add`, `rename <slug>`, `remove <slug>`. |
| `ccs nick <slug> [name]` | Set or clear the nickname. Empty falls back to the email. |
| `ccs color <slug> <n>` | Set the palette index, 0–7. |
| `ccs display <slug> <mode>` | `nickname` \| `index` \| `claude code` \| `icon only`. |
| `ccs hide <slug> toggle\|on\|off` | Render the glyph invisible but still clickable. |
| `ccs headless [<slug>]` | Show or set which account answers `ccs -p`. Exclusive: setting one clears the rest. |
| `ccs dangerous [<slug>]` | Show or toggle `--dangerously-skip-permissions` per account. |
| `ccs usage [<slug>]` | Both quota windows, their age and their source. |
| `ccs statusline [<delegate>…]` | The recording hook. Wired by hand in `~/.claude/settings.json`. |
| `ccs poll [<slug>] [--force]` | Fetch each account's usage without a session. What the systemd timer runs. |
| `ccs relink [<slug>]` | Self-healing symlink pass. All accounts if the slug is omitted. |
| `ccs render <slug>` | Print the Waybar label. Nothing else — this runs every 30 s, per account. |
| `ccs config` | Rewrite the managed block and reload Waybar. |
| `ccs launch <slug> <mode> [arg]` | `new`, `last`, `resume <uuid>`, `search`. |
| `ccs default [<slug>]` | Show or set the account used when nothing else decides. |
| `ccs <slug> <cmd…>` | Run `<cmd…>` under that account's identity. |

## Terminal use

There is no shell function. `ccs` is the entry point, so nothing shadows
`claude` and nothing has to be re-sourced.

Anything starting with a flag is claude's, not ours, and runs under the account
`ccs headless` names — asked once, on the first interactive run that needs an
answer, then remembered:

```bash
ccs -p "say hi"
ccs -c
ccs -r
ccs -- mcp list               # claude's own subcommands need the --
ccs work claude auth status   # or aim at one account explicitly
```

Pipes, scripts and cron never see a prompt: with no terminal attached the
default account answers rather than blocking on a stdin nobody can type into.

## Usage limits

Each account can report its own 5-hour and 7-day quota, shown as two bars in the
panel. The reading comes from Claude Code's own statusline hook, so **CCAS never
wires it for you** — every account's `settings.json` is a symlink to
`~/.claude/settings.json`, and writing it would break the invariant above.
`ccs doctor` prints the exact line to add:

```json
{ "statusLine": { "type": "command", "command": "ccs statusline <your-statusline>" } }
```

`ccs statusline` is a wrapper, not a statusline: the same bytes go to your
delegate, its stdout is passed through verbatim and its exit code becomes ours.
Every recording failure is swallowed, because a statusline that raises is
visible in every prompt of every session.

The hook only fires inside a session, so it serves the account you are working
in and nothing else — close the last session and that account's numbers freeze.
A systemd user timer therefore runs `ccs poll` every five minutes, which asks
the endpoint Claude Code itself asks using the token already in the account's
credentials file. The cadence lives in the unit and nowhere else:

```bash
systemctl --user status ccas-poll.timer   # is it running
systemctl --user edit ccas-poll.timer     # retime it
systemctl --user stop ccas-poll.timer     # stop it
journalctl --user -u ccas-poll            # why a poll failed
```

CCAS **reads** that credentials file and never writes it — refreshing a token
risks the account's login and can invalidate the one a live session holds. So
polling keeps an account fresh only while its access token is valid, about eight
hours. Past that the account goes quiet until it is next used, which is honest
rather than broken: an account idle that long has rolled its 5-hour window over,
and that is reported from the timestamp alone.

What is displayed is the **reset time**, not the percentage. `resets_at` is an
absolute anchor: past means the window rolled over, and future makes the
recorded percentage a lower bound — hence `≥`. See
`docs/usage-limits-research.md` for what was measured, and
`docs/superpowers/specs/2026-07-27-usage-poll-design.md` for why the poll exists.

## Notes

- The bar's click no longer generates anything. CCAS used to write a `menu.xml`
  per account, and Waybar parses `menu-file` once when the module is built — so
  keeping it current meant reloading the whole bar every time a session
  appeared. The only thing that still reloads is a change to the *set* of
  modules; every other setting is a per-module signal.
- Colour, display mode and hide state are Pango markup in the `exec` output, so
  they refresh flicker-free via that signal and need no CSS.
- CCAS writes the Waybar *config*, never your `style.css`, and never your
  `~/.config/ccas/menu.css` after the first copy. Both are yours.
  See `docs/waybar-setup.md` for the styling CCAS does not own.
- History is rebuilt from scratch on every scan — 219 sessions in about 10 ms,
  by regex over a 64 KB head rather than JSON parsing. There is no cache to go
  stale.
- `~/.claude.json` cannot be shared, because `CLAUDE_CONFIG_DIR` relocates it
  into the account directory and it holds the account identity. It is seeded
  from yours minus `oauthAccount`, so project trust and MCP servers carry over,
  then drifts per account.

## Development

```bash
python -m pytest        # ~330 tests, under a second; pytest is dev-only
```

`CLAUDE.md` is the rules that must not be broken. `docs/why.md` is the story
behind them — every bug found on the real system, with the symptom and the
cause.
