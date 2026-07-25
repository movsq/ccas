# CCAS — Claude Code Account Switcher

Switch between multiple Claude Code accounts from Waybar and from the terminal,
changing authentication only. Settings, plugins, themes and session history stay
shared, because they are shared by reference rather than copied.

Each account owns a coloured `✻` in Waybar. Clicking it opens a native GTK menu
offering a new session, resume-last, a searchable history of every past session,
and per-account display controls.

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
`~/.local/share/ccas/`, and writes fenced managed blocks into
`~/.config/waybar/config.jsonc` and `~/.bashrc`. Both files are backed up
alongside themselves as `*.ccas-orig` before the first modification, and only
the region between the sentinels is ever touched.

With no accounts configured, a dim `✻` appears in Waybar; click it to add your
first account.

```bash
./uninstall.sh            # strips the managed blocks, leaves account data
./uninstall.sh --purge    # also moves ~/.cc-accounts to ~/.claude_trash
```

Nothing is ever deleted. Removal always means a move to `~/.claude_trash/`.

## Commands

| Command | Behaviour |
|---|---|
| `ccs list` | Table of accounts: index, slug, email, colour, display mode. |
| `ccs add` | Create the directory, relink, seed config, run login, record the email. |
| `ccs rm <slug>` | Move the account directory to `~/.claude_trash/`, drop it from the registry. |
| `ccs nick <slug> [name]` | Set or clear the nickname. Empty falls back to the email. |
| `ccs color <slug> <n>` | Set the palette index, 0–7. |
| `ccs display <slug> <mode>` | `nickname` \| `index` \| `claude code` \| `icon only`. |
| `ccs hide <slug> toggle\|on\|off` | Render the glyph invisible but still clickable. |
| `ccs relink [<slug>]` | Self-healing symlink pass. All accounts if the slug is omitted. |
| `ccs render [<slug>]` | Print the Waybar label; regenerate `menu.xml` and `history.tsv`. |
| `ccs config` | Regenerate the managed blocks and reload Waybar. |
| `ccs launch <slug> <mode> [arg]` | Launch a session: `new`, `last`, `hist <N>`, `search`. |
| `ccs default [<slug>]` | Show or set the account used for argument-carrying commands. |
| `ccs tty [args…]` | Terminal entry point. No args opens the picker. |
| `ccs manage <action>` | GUI management from the menu: `add`, `rename <slug>`, `remove <slug>`. |
| `ccs <slug>` | Open that account's mode menu. |
| `ccs <slug> <cmd…>` | Run `<cmd…>` under that account's identity. |

## Terminal use

The installer adds a `claude()` shell function. Bare `claude` opens the account
and mode pickers. **With arguments it shows no menu** — the command runs
immediately under the account named by `default`, so `claude -p "…"` behaves as
it always did. To aim arguments at a specific account, use the passthrough:

```bash
ccs work claude -p "say hi"
ccs work claude auth status
```

## Notes

- Waybar caches `menu-file` at startup; neither the 30 s `exec` tick nor a
  per-module `SIGRTMIN` signal makes it re-read. `ccs render` therefore triggers
  a full reload, but only when the session set actually changed. See
  `docs/superpowers/spike-waybar-menu.md`.
- Colour, display mode and hide state are Pango markup in the `exec` output, so
  they refresh flicker-free via the per-module signal and need no CSS.
- Styling the modules — spacing, hover, press feedback, glyph size — is set up
  by hand once. CCAS writes the Waybar *config*, never your `style.css`.
  See `docs/waybar-setup.md`.
- History is rebuilt from scratch on every render — 219 sessions in about 10 ms,
  by regex over a 64 KB head rather than JSON parsing. There is no cache to go
  stale.
- `~/.claude.json` cannot be shared, because `CLAUDE_CONFIG_DIR` relocates it
  into the account directory and it holds the account identity. It is seeded
  from yours minus `oauthAccount`, so project trust and MCP servers carry over,
  then drifts per account.

## Development

```bash
python -m pytest        # stdlib only at runtime; pytest is dev-only
```
