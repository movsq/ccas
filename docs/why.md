# CCAS — why the code looks like this

The story behind the rules in `CLAUDE.md`. Each section is a bug that was found
on the real system, or a decision that came out differently from the plan, with
the symptom and the cause — because several of those rules look arbitrary
without it, and a rule you do not understand is a rule you will "simplify".

Nothing here is status. It is history, and it does not go stale: add a section
when a new bug is found, and leave the rest alone.

Background from the build session: the spec is
`docs/superpowers/specs/2026-07-25-ccas-design.md`, the plan
`docs/superpowers/plans/2026-07-25-ccas.md`, the Waybar spike
`docs/superpowers/spike-waybar-menu.md`, and the first live integration run
`docs/superpowers/task-11-live-integration.md`.

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
| 10 | The menu is headed by the email, with the nickname on a second row | The plan's single nickname-or-email title hid the address the account is actually identified by. |
| 11 | The render tick writes `menu.xml`/`history.tsv` **only** when it is also going to reload | Reordering is not a change (see "The bar resetting itself"), and writing without reloading desynchronises the cached rows from the tsv the actions index. `ccs config` is the unconditional rebuild. |
| 13 | `claude -p` resolves an account instead of silently using the default | User request. See "The headless runner" below. |
| 14 | CCAS no longer installs a `claude()` shell function; `ccs -p …` is the terminal entry point | User's call: keep the real `claude` theirs. See "The passthrough" below. |
| 15 | `ccs doctor` audits the install and never repairs it | A repair would mask the fault. See "The doctor" below. |
| 16 | An account directory is named after the **email**, not the nickname | The nickname is optional and the directory is not. See "Why the nickname named the directory" below. |
| 12 | `Hide icon` is marked `●`/`○`, not `☑`/`☐` | FontAwesome sits first in the bar's font stack and covers U+2611 but not U+2610. See "The Hide icon box never looked checked". |

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
through `waybar._ccs()`, which appends the flag. The `ccs -p` passthrough
deliberately does **not** — it is the terminal path and must use fzf. Two tests
pin the pair (`test_generated_commands_all_force_gui_mode`,
`test_passthrough_does_not_force_gui_mode`), because this exact mistake was made
and caught during the session, back when a bashrc function was the terminal
path.

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

### The bar resetting itself every few minutes

Reported live: Waybar "just reset for no reason", repeatedly. It was not a
crash — the process was 1 d 17 h old throughout — it was our own `SIGUSR2`.

`session_set_changed()` compared the **newest** uuid. With two Claude sessions
alive, whichever was last written to becomes the newest, so every switch
between them looked like a changed session set and the 30 s tick reloaded the
whole bar. Four hand-overs in the 90 minutes before the report, in
`~`, `~/4s` and `~/ccas`.

It now compares the **set** of uuids, so reordering is not a change; only a
genuinely new session is. Diagnose the same class of thing with:

```bash
ps -o pid,lstart,etime -C waybar          # same pid ⇒ a reload, not a restart
python -c "from ccas import history, menu; print(menu.session_set_changed('vsed', history.scan()))"
```

The write is now bound to the same condition, which is the subtle half. Waybar
is still showing the menu it cached at the last reload, and `hist-i` resolves
against line *i* of `history.tsv` — so rewriting the pair every tick *without*
reloading would have left the visible row and the session it launches pointing
at different sessions. Write and reload together, or not at all.

`session_set_changed()` also has to return True when no snapshot exists at all:
an account with no history would otherwise compare empty-to-empty and never get
its `menu.xml` written. That regression was caught by
`test_render_regenerates_the_menu_file`.

### The headless runner

`claude -p "say hi"` ran under `reg["default"]` with nothing showing which
account that was. Requested shape, and the one implemented: mark an account as
the runner once and never think about it again. Choosing the marked account
clears it, which is how the one-time prompt comes back.

Three ways in, all the same toggle (`cli._toggle_headless`): the
`○ Headless runner` row in the Waybar menu, a row at the bottom of the terminal
mode menu (`ccs <slug>` or bare `ccs`, after the account pick), and
`ccs headless [<slug>]` directly. The terminal row is worded
`○ Select as headless runner` / `● Headless runner — pick to clear` rather than
reusing the bar's label: among four verbs in a flat fzf list the mark alone read
as a status line, not as something pickable (user feedback). The mode-menu row is checked **before** the
launch branches, because `"All projects…"` is that dispatch's catch-all and would
otherwise swallow it.

`cli._runner_slug()` resolves, in order:

1. `CCAS_ACCOUNT=<slug>` — one run only, never remembered.
2. `NO_ACCOUNT_ARGS` (`--version`, `doctor`, `update`, …) → the default account,
   no prompt. A **deny**-list on purpose: an unrecognised argument is far more
   likely to start a session than not, and erring the other way runs it under an
   account the user never chose.
3. The marked runner, if there is one.
4. **stdin is not a terminal** → the default account, silently. `echo hi | claude
   -p`, scripts and cron must never meet a prompt; that is the same shape as the
   `is_gui` bug — blocking on a stdin nobody can type into.
5. Otherwise ask via fzf, remember the answer, and continue. Asked even when only
   one account exists: the point is knowing which one answered.

`headless` is a per-account boolean kept exclusive by `registry.set_headless()`,
not a top-level key, so `menu.build_xml()` stays a pure function of the account
dict it renders — the same shape as `hide_icon`. `registry.load()` backfills it
for registries written before it existed, and `remove()` clears it with the
account so a deleted slug cannot be resolved later. Setting it rewrites **every**
account's menu (`cli._refresh_all`), because turning it on for one turns it off
for the rest.

### The passthrough

Until 2026-07-25 the installer wrote a `claude()` function into `~/.bashrc` so a
bare `claude` resolved an account. The user asked to drop it — *"keep the original
claude and `ccs -p` would be our thing instead of replacing global original"* —
and they were right: shadowing bought nothing the passthrough does not, only
reached new shells, and was the sole reason a bare `claude` call from inside the
code loops forever.

`cli.main()` now routes any first argument beginning with `-` (`ccs -p …`, `-c`,
`-r`) straight to `cmd_tty`, which resolves the runner account and execs the real
binary. `--` is the escape for claude's own *subcommands* (`ccs -- mcp list`),
which would otherwise collide with `ccs list`/`add`/`render`. Both branches sit
after the `--gui` strip — `--gui` is ours, never forwarded — and before the
account-slug lookup.

`waybar.apply_bashrc()`/`bashrc_block()` are gone, replaced by
`waybar.strip_bashrc()`: an install now *removes* the old block. It only rewrites
when the block is actually present, so an unmanaged `~/.bashrc` keeps its mtime
and never grows a `.ccas-orig` for a file we never touched. Existing shells keep
the stale function until they are restarted, which is why `paths.claude_bin()`
remains mandatory.

### The doctor

`ccas/doctor.py`. CCAS writes into four places that drift apart — `config.jsonc`,
`~/.bashrc`, the account directories, the registry — and every bug found on the
real system was drift between them rather than a logic error. Each was caught by
hand; `doctor.run()` is those checks in one pass, returning `Check(ok, label,
detail)` records that `cli.cmd_doctor` prints, rc 1 if any failed.

What it asserts: no symlinks in `~/.claude` (the invariant everything rests on,
and deliberately the first line of output); both binaries exist and are
executable; no legacy `claude()` block in `~/.bashrc`; `default` points at a real
account and at most one account is marked `headless`; the Waybar block lists
exactly the registry's modules and they sit in `HOST_LIST`; and per account, that
every symlink resolves under `~/.claude`, that nothing shared is unlinked, and
that `menu.xml`/`history.tsv` exist.

Account rows are labelled by `label.display_name()` — the nickname, falling back
to the email — not by the slug: "i still see vsed in doctor" after a rename, and
the slug is a filesystem detail. It stays in the detail line, next to the path
and the command that fixes it.

**It never writes** — not even a `relink`, tempting as that is: a doctor that
repairs on sight masks the fault it was run to find. Every failing check names
the command that fixes it instead (`ccs config`, `ccs relink`, `./install.sh`).

Verified live 2026-07-25: all 14 checks green on the real install, and a negative
control run (`CCAS_CLAUDE_BIN=/nonexistent CCAS_BASHRC=<a fake with the old
block> ccs doctor`) flagged exactly those two and exited 1.

### Why the nickname named the directory

User's question, and a fair one: *"how comes a nickname was THE DIRECTORY, if
nicknames arent mandatory?"* Ordering. The account directory **is**
`CLAUDE_CONFIG_DIR`, so it must exist before `claude auth login` runs — and the
email, the obviously right name for it, is only known once that login returns.
The nickname was the sole string available at that moment, so `cmd_add` fell
back to `slugify(nickname or "account")`; adding without a nickname produced a
directory literally called `account`, then `account-2`.

Fixed 2026-07-25: `cmd_add` now creates the directory under that placeholder,
and once `auth_status` reports the email, `accounts.rename()` moves it to
`slugify(email)`. That window is the one time a rename is free — login has
exited, the registry does not mention the account yet, no Waybar module or menu
names it, and the symlinks inside are absolute paths under `~/.claude` so a move
does not disturb them. A `rename`, never a fresh `mkdir`: the login has already
written `.credentials.json` in there.

`_free_slug()` checks the registry **and** the disk. A directory with no registry
entry is possible (a half-finished add, a restore from trash) and reusing one
would put two accounts in a single `CLAUDE_CONFIG_DIR`.

Existing slugs are untouched — `vsed` and `vo-se-15th` keep their names, since
the slug is the directory name and the Waybar module id. Renaming those would
mean moving a directory a live session may hold open.

**Found while verifying this:** a sandbox `ccs add`, audited with `ccs doctor`,
reported the new account's `menu.xml` and `history.tsv` missing. `cmd_add` never
wrote them — the render tick heals it within 30 s (`session_set_changed()`
returns True when no snapshot exists), but until then the new icon opened a menu
Waybar could not read. `cmd_add` now writes the menu before `waybar.apply()`
points at it. First bug the doctor caught on its own.

### The Hide icon box never looked checked

Reported live: "the togglebox for hidden icon doesn't fill when enabled." The
state was persisting correctly and `menu.xml` really did say `☑` — the glyph was
the problem.

`~/.config/waybar/style.css` sets
`font-family: FontAwesome, "JetBrainsMono Nerd Font Mono", monospace`, and
FontAwesome is consulted first for every codepoint it covers. It covers `☑`
(U+2611) and not `☐` (U+2610), so the checked box came from FontAwesome and the
unchecked one fell through to DejaVu — different sizes, different weights, and at
11 px the "on" state was a small faint box that read as empty. Rendered with
`pango-view` against that exact stack to confirm it rather than guess.

`menu.MARK_ON`/`MARK_OFF` (`●`/`○`, U+25CF/U+25CB) are now the single vetted
pair, shared by the display rows, the colour rows and this toggle. Vet any new
glyph the same way — the `pango-view` command is in `CLAUDE.md`.

The fix also exposed that `ccs render` no longer forces a rebuild (it writes only
when it reloads), so the new XML never reached the menus on disk. `ccs config`
now rewrites every account's menu unconditionally, which is what `install.sh`
runs — so "re-run `./install.sh` after any code change" is true again.

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

---

## Backups

Backups of the pre-CCAS originals:
`~/.config/waybar/config.jsonc.pre-ccas`, `~/.bashrc.pre-ccas`, plus
`*.ccas-orig` copies written by the installer.

An account slug can carry a suffix from the de-duplicator: `vo-se-15th` is what
`vo.se` became when that account was removed and re-added while testing Manage.
Slugs are permanent once written, so the suffix stays.
