# Task 11 — live integration results

**Date:** 2026-07-25
**Status:** Steps 1–3 and 7 verified. Steps 4–6 deferred at the user's request
(they were away from the machine; the OAuth browser flow needs a real
interactive session).

## Verified

### Step 1 — backups
`~/.config/waybar/config.jsonc.pre-ccas` and `~/.bashrc.pre-ccas` written.
`install.sh` additionally wrote its own `*.ccas-orig` copies.

### Step 2 — placeholder appears
`./install.sh` completed. The dim `✻` renders at the right end of the bar,
after the clock, at the `alpha='28000'` opacity `placeholder_config()`
specifies. Confirmed from a `grim` screenshot rather than by eye.

### Step 3 — real config survived
```
custom/stopwatch            2 occurrences (unchanged)
CCAS start sentinel         1
CCAS end sentinel           1
modules-right               ["custom/stopwatch","pulseaudio","clock","custom/cc-setup"]
```
The file still parses as JSON once `//` lines are stripped. The `claude()`
function is present in `~/.bashrc` exactly once, below the user's own content.

### Step 7 — uninstall restores the originals
```
diff ~/.config/waybar/config.jsonc ~/.config/waybar/config.jsonc.pre-ccas  → empty
diff ~/.bashrc ~/.bashrc.pre-ccas                                          → empty
```
`ccs` and the staged package were moved to `~/.claude_trash/`, not deleted.
`./install.sh` was then re-run to leave the system installed.

### Isolation guarantee
`~/.claude/.credentials.json` mtime was `1784954438` before install and
`1784954438` after install, uninstall and reinstall. **CCAS did not write to
`~/.claude`.** This is also asserted in the sandbox by
`test_relink_never_touches_mtimes_in_claude_home`.

## Not yet verified

These need an interactive session and a browser:

- **Step 4** — `ccs add`, the OAuth login, and `claude auth status` reporting
  `loggedIn: true` under the account directory. The symlink farm itself was
  proven during design; what remains unproven is CCAS's own add flow end to end.
- **Step 5** — the menu: title row, history submenu with real sessions, the four
  display modes, the hide-icon latch firing exactly once per entry into the
  invisible combination.
- **Step 6** — both launch paths: kitty from the menu, and the terminal
  `claude` / `claude -p "…"` / `ccs <slug> claude auth status` behaviours.

To resume, run `ccs add` in a terminal (or click the dim `✻`) and work through
Steps 4–6 of `docs/superpowers/plans/2026-07-25-ccas.md`.
