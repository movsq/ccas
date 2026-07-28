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
| 2 | `install.sh` moves the old staged copy to trash instead of `rm -rf` | Global CLAUDE.md: never delete. The trash is `.claude_trash/` in the checkout, not `$HOME/.claude_trash` — the rule names a relative path, and the `$HOME` reading was a later invention. |
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
| 17 | The usage row is the picker's `--mesg`/`--header`, not a menu row | The spec was written against `menu.xml`, which was retired before it was built. See "The usage row outlived the menu it was designed for" below. |
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
`test_passthrough_does_not_open_the_panel`), because this exact mistake was made
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

### The glyph floated above its own label

Enlarging the bar glyph (`ICON_SIZE = x-large`) made it sit visibly higher than
the text beside it: pango aligns the two runs on a shared baseline, and ✻ has
more of its ink above that baseline than a digit does. `ICON_RISE` drops the
glyph back down — it is a baseline shift in 1024ths of a point, and `-800` is
what measured flush on the real bar.

That left the glyph a pixel taller than the 11px text, which read as the text
floating rather than the icon. `TEXT_SIZE = 110%` raises the text to match, so
both runs now ink rows 8–17 of the bar. Measured, not eyeballed: crop the bar
out of `grim -o DP-1` and take the min/max ink row per column group — the four
runs must report the same pair.

### A bar started from inside an agent session silently killed transcripts

Sessions opened by clicking the bar stopped saving transcripts — `⚠ Transcript
saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker`. The same account
opened by typing `ccs`, or by typing `claude`, saved normally. That asymmetry is
the whole diagnosis: nothing in CCAS sets that variable (`accounts.env_for` sets
only `CCAS_INNER`), so it had to be arriving from the parent process, and only
one parent differs between the two paths.

Waybar itself was the carrier. It had been restarted mid-session from a Claude
Code `Bash` call — a broken intermediate `label.py` took the bar down — so it
inherited that session's whole environment: `CLAUDE_CODE_CHILD_SESSION=1`,
`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CONFIG_DIR`, even `CLAUDE_EFFORT`.
Claude Code reads the child marker as "you are a nested sub-session" and disables
transcript persistence. Every `ccs --gui launch` off the bar inherited it; every
terminal Sway spawned did not.

The fix is to restart the bar from a scrubbed environment — not to unset the
variable in CCAS, which would paper over a poisoned parent while leaving
`CLAUDE_CONFIG_DIR` and the rest of it in place:

```bash
killall waybar
setsid env $(env | grep -oE '^(CLAUDE[A-Z_]*|CCAS[A-Z_]*|AI_AGENT)=' \
             | tr -d = | sed 's/^/-u /') waybar >/dev/null 2>&1 &
```

Two lessons outlast this bug. **A long-lived process started from an agent shell
carries that shell's environment for its entire life**, and it hands it to every
child — so restarting a daemon from inside a session is not equivalent to
restarting it from a terminal. And the diagnosis is a `/proc` walk, not a guess:
climb `stat` field 4 from the affected process and print
`grep -c '^CLAUDE_CODE_CHILD_SESSION=' /proc/$p/environ` at each hop. The
variable appears at the exact hop that introduced it. Reading `env` inside the
agent's own `Bash` tool proves nothing — that tool sets the marker itself.

---

### It came back, and the lesson above was too narrow

`⚠ Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker`, again,
from a session started at the bar. This time Waybar was innocent —
`grep -c CLAUDE_CODE_CHILD_SESSION /proc/$(pgrep -x waybar)/environ` returned 0,
and so did kitty's, so the scrubbed restart above had held.

The carrier was the **GTK panel**, left open on screen by an agent that had
launched it from a `Bash` call to verify Task 7. A panel is not a daemon and
nobody thinks of it as long-lived, but it does not have to be: its launch verbs
run `kitty … claude` with `accounts.env_for()`, which is `dict(os.environ)` —
the agent shell's, marker and all. Anything still running that can *launch* is a
carrier, for exactly as long as it is running. That is the general rule; "don't
restart a daemon from an agent shell" was the special case of it.

So the judgement in the section above is reversed on one point. It rejected
unsetting the variable in CCAS as papering over a poisoned parent. That was right
about the diagnosis and wrong about the remedy, because CCAS is never a nested
session: `accounts.env_for()` is the single door every launch path goes through,
and the marker is false of everything on the other side of it.
`PARENT_SESSION_VARS` drops `CLAUDE_CODE_CHILD_SESSION`, `CLAUDECODE` and
`CLAUDE_CODE_ENTRYPOINT` there. The original objection — that unsetting leaves
`CLAUDE_CONFIG_DIR` and the rest in place — does not survive contact with the
code: `env_for` sets `CLAUDE_CONFIG_DIR` itself, on the next line.

This does not retire the scrubbed restart. It stops CCAS from *propagating* a
poisoned environment; it cannot clean one that a supervising process is still
holding, and a bar carrying `CLAUDE_CONFIG_DIR` from an agent shell is still
wrong in ways no scrub inside CCAS can see.

---

### The usage row outlived the menu it was designed for

`docs/superpowers/specs/2026-07-25-usage-limits-design.md` puts the per-account
usage row in `menu.xml`, as an insensitive `GtkMenuItem` under the email and the
nickname. Between that spec and its implementation, `menu.xml` was retired
altogether — the bar's click now runs `ccs --gui <slug>` and the rows are
generated fresh per click.

So the row moved to where that screen now lives: `pickers.choose(..., note=…)`,
which is fuzzel's `--mesg` and fzf's `--header`. Both put text above the rows
that cannot be selected, which is what "insensitive" bought in the GtkMenu, and
both honour a newline, so the second line the 7-day window sometimes needs costs
nothing.

Two paragraphs of the spec died with the menu and are worth not re-deriving:

- The spec argues at length that **a usage change must never trigger a reload**,
  because a cached `menu.xml` can only be refreshed by SIGUSR2 and that would put
  back the flicker `session_set_changed()` was written to remove. There is no
  cache left. The picker reads `usage.read()` at the moment it opens, so the row
  is never stale, and the argument is moot rather than wrong.
- It also lists verifying `·` (U+00B7) against **Waybar's FontAwesome-first font
  stack**. Nothing usage-related renders in that stack any more: the bar label
  carries only digits, `:`, `%` and `7d`, and `·`/`≥` appear only in fuzzel and
  fzf. `fc-list ':charset=00b7'` and `:charset=2265` both resolve there, and a
  `pango-view` render of the finished row is clean.

What did still need measuring was the label, and it holds: rendered at the bar's
own font, the clock run inks rows 16–25 — the nickname's exact band — and the
glyph stays at 15–26 whether the third run is there or not. `ICON_RISE` did not
move.

### The bar's picker inherited a "here" the bar does not have

Retiring `menu.xml` converged two screens onto one, and the merge went the wrong
way. The plan says to splice the GtkMenu's three appearance rows *into* the
terminal's list, so what a bar click opened was the terminal screen with three
rows added: `New here  (~)`, `Resume last in  ~`, `History in  ~…`, `All
projects…`. The GtkMenu's own verbs — `New session`, `Resume last session`,
`Resume from history` — never named a directory, and that was not an oversight.

`cmd_mode_menu` takes its scope from `os.getcwd()`. Under a terminal that is the
directory the user is standing in, which is the entire point of that screen.
Under a bar click it is *Waybar's* working directory, `~` or wherever the
compositor was started — a directory the user never chose and cannot see. Two of
the three labels were also lying: `launch.resolve` filters its rows on cwd only
`if cwd is not None and not gui`, so `Resume last in ~` resumed the globally
newest session and `History in ~…` listed every project. Only `New here` obeyed
the label, and it obeyed it by starting a session in `~`.

So the launch verbs are now the one part of the screen that differs by door:
`gui` gets the GtkMenu's three, and is handed `scope = None` so `resolve()` is
told what it was already doing. Everything below them — appearance, the two
runner toggles, `Manage…` — is identical from both, in the GtkMenu's grouping.

The general shape of this: a screen that merges two callers inherits the
assumptions of whichever one it was copied from, and `os.getcwd()` is the
assumption that survives a merge invisibly, because it is never passed in.

### The layer surface that was an ordinary window

`gtk4-layer-shell` has to be loaded before `libwayland`. PyGObject loads
`libwayland` first, so `Gtk4LayerShell.init_for_window()` did nothing: the panel
opened as a plain `xdg_toplevel`, on whichever output the compositor picked —
the Dell, not the bar's screen — with `GtkWindow is not a layer surface` on
stderr and four more warnings under it. Nothing crashed. It just was not a menu:
no anchoring, no click-outside dismissal, and it showed up in the window list.

The library's own advice is `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so`. That
would have to live in the `ccs` launcher, and `ccs` execs `claude` — so every
session launched from the bar would inherit the preload for the rest of its
life. `ctypes.CDLL("libgtk4-layer-shell.so.0", mode=ctypes.RTLD_GLOBAL)`
immediately before `import gi` is the same fix scoped to the one process that
needs it, and `panel_ui.show()` does that first, before anything else.

The second half of the same bug: a layer surface with no monitor set goes
wherever the compositor puts it, which on two heads is not reliably the one
Waybar is on. `CCAS_PANEL_OUTPUT` names a connector; unset keeps the
compositor's choice. Naming a disconnected output is deliberately not an error —
the monitor list changes when a cable does, and a panel that refuses to open is
worse than one on the wrong screen.

Both were found by opening the window and sampling pixels, not by reading the
code: the failure is silent, and the only symptom is *where* the thing appeared.

### A menu you cannot get out of

Reported the day after it shipped, four complaints in one: clicking the widget
again did not close the panel, there was no close button, clicking away from it
did nothing, and Escape worked "very inconsistently". Underneath them was a
fifth — it sometimes opened on the monitor the user had not clicked on.

They are one bug in three parts.

**The surface was too small.** Anchored to the top edge only, the panel occupied
its own 900×620 and nothing else, so a click beside it was never delivered to
CCAS at all: it went to whatever was underneath. There is no "clicked away"
event to listen for. The surface now anchors to all four edges and the panel
floats on it, which makes "outside" a hit test against the panel's bounds rather
than something the compositor could have told us. That also settles the
constraint the user put on the feature — the click must not reach the
application beneath. Wayland delivers a pointer press to exactly one surface, so
a click that closes the panel is by construction a click that did nothing else;
it cannot pause a video. Exclusive zone stays 0, which keeps the surface out of
the space Waybar reserved and leaves the widget that opened the panel clickable.

**The keyboard was taken on demand.** `ON_DEMAND` gives a layer surface the
keyboard only once it has been clicked, which is exactly the inconsistency
reported: Escape worked if you had touched the panel first and not otherwise —
and never at all when it opened on the far monitor, because the pointer was
nowhere near it. `EXCLUSIVE` takes the keyboard at map time. It sounds like the
riskier setting and is not, here: sway resolves its own bindings before
forwarding, so the compositor cannot be locked out, and there are now three
independent ways to close the panel. The Escape handler also had to move to the
window in the CAPTURE phase — the search entry has the focus from the moment the
panel opens, and a `GtkSearchEntry` eats Escape to clear itself.

**Nothing said which output.** `CCAS_PANEL_OUTPUT` was the only answer and it is
a manual one. sway focuses the output under the pointer, so the focused output
is the monitor whose bar was just clicked; `panel.current_output()` asks
`swaymsg -t get_outputs` for it and the env var still wins when set. A
non-sway session gets None and the compositor's choice, as before.

The toggle is separate and smaller: the panel writes its pid and slug to a lock
in `XDG_RUNTIME_DIR`, and `cmd_panel` closes whatever is open before opening
anything. Same slug means the widget was clicked twice and nothing reopens;
a different slug is a switch between accounts. `SIGTERM` does not run a finally
block, so the process being closed never releases its own lock — the sender
clears it, and a lock naming a dead pid reads as nothing being open.

Verified on the real system rather than argued: the panel opened on DP-1 with
the pointer there and on HDMI-A-1 with the pointer *not* there, and a synthetic
Escape from a uinput keyboard closed both without the pointer ever entering the
window. There is no `wtype` or `ydotool` on this machine; `swaymsg` moves a
pointer but cannot type, so the virtual keyboard was the only way to ask the
compositor the real question — which is who it hands a keypress to.

### Which monitor, part two: the focused output is not the pointer's

The first fix asked sway for the focused output. That is wrong here, and the
config says why in one line: `focus_follows_mouse no`. The focused output is the
one holding the focused *window*, so the panel followed whatever was last
clicked into rather than the bar that was just clicked — reported as "it always
opens on the monitor where I have a window focused".

There is no better answer to ask for. sway's IPC has no cursor position at all:
`get_outputs` has a `focused` flag and `get_seats` has devices, and that is the
whole of it. Wayland tells a client nothing about the pointer until the pointer
is over one of that client's surfaces.

Which is the answer, and it is what fuzzel does for the same question: put a
surface on **every** output and see which one the pointer is already inside.
`panel_ui._pointer_output()` maps one transparent probe per monitor, takes the
`wl_pointer.enter`, and builds the real panel on that connector; the probes are
dropped after the panel exists, so the application never runs out of windows.
Exclusive zone -1 on them deliberately — they must cover the bar, because the
bar is exactly where the pointer is one moment after its widget was clicked.

Two things were measured rather than assumed:

- **A GTK4 window that paints nothing gets no input region**, and a layer
  surface with no input region receives no pointer events at all. The first
  probe used `background: transparent` and came back empty every single time, on
  both monitors; `rgba(0,0,0,0.01)` — under a quarter of one 8-bit step, so
  still invisible — fixed it outright. The same trap sits under `.ccas-scrim`:
  setting it fully transparent in `menu.css` would silently stop the panel
  closing on an outside click *and* let that click through to whatever is
  beneath. `assets/menu.css` says so where someone would be about to do it.
- **40 ms is too early and 80 ms is enough** for the enter to arrive after the
  surface maps. `PROBE_MS` is 120.

**A synthetic pointer is not a pointer.** The probe answered correctly on DP-1
every time, atomically — the cursor placed by the probe process itself, the right
connector back. On HDMI-A-1 it never answered, and neither did anything else:
with the panel pinned there a click outside it did not close it, and a probe on
the TOP layer was as silent as one on OVERLAY, while `grim -c` showed the cursor
sitting inside the probe's own tint. Every reading said the surface was mapped
and the pointer was over it.

It was the test. `swaymsg seat - cursor set/move` does not make sway recompute
pointer focus on an output where no real input is happening; DP-1 passed because
a hand was on the mouse there, generating the motion that does. The user clicked
the widget on the other monitor and the panel opened on it, first try.

The lesson is narrower than "automate the check" and worth keeping: **a
synthetic pointer can move the cursor but cannot always make the compositor act
on where it now is.** `swaymsg cursor` is enough to drive a widget on the output
the user is already working on — that is what the recipe under Working style is
for — and is not evidence about an idle one. The keyboard has no such problem: a
uinput device is a real device, and that is why the Escape checks above hold
without an asterisk.


---

---

---

## "Icon only" was an icon and a clock

The four display modes were named for what they showed, and none of them was
telling the truth: `label.render()` appended the usage reading to every one of
them, so the mode called "icon only" put a glyph and a percentage on the bar.
There was no way to ask for an icon on its own, and no way to ask for the
percentage without the reset time, or the reset time without the percentage —
the clock was one string, decided in `label.py`, the same for everybody.

The fix was to make the names honest and move the choice out of the code. The
clock came out of all four built-ins, and a fifth mode, `custom`, took a format
string of tokens (`ccas/format.py`). Nothing was lost: `%icon %name %5h` is the
default format and renders byte for byte what the old `nickname` mode did, which
is what `test_the_default_format_reproduces_the_old_bar` pins.

Two decisions inside it are worth keeping:

**An unknown token renders literally.** `%5hh` puts the text `%5hh` on the bar
rather than being rejected at the prompt. A visible wrong answer explains itself
and the fix is one more `ccs format` away; a refusal at the prompt leaves the
user guessing which of their tokens was the bad one. `ccs doctor` names it on
every run so it cannot be forgotten, and — like everything else in doctor — it
reports rather than repairing, because rewriting the format there would erase
the typo the check exists to show.

**`custom` was appended to `DISPLAY_MODES`, never inserted.** Both `ccs display`
and the panel dropdown addressed a mode by its index in that list, so inserting
one at the front would silently repoint every account's existing selection.
The modes are retired now — the format string is the only way a label is built,
and "icon only" is the string `%icon` — but the hazard is worth keeping: any
list a stored value indexes into is a list you can only append to.

The panel's switch row was also split at this point. It had been one flat line
of checkboxes plus a dropdown, which was fine at three items and unreadable once
the token colour row arrived. Now the two things that change how the account
*runs* — headless runner, skip permissions — sit above a rule, and everything
that changes how its widget *looks* sits below it under a caption.

## Backups

Backups of the pre-CCAS originals:
`~/.config/waybar/config.jsonc.pre-ccas`, `~/.bashrc.pre-ccas`, plus
`*.ccas-orig` copies written by the installer.

An account slug can carry a suffix from the de-duplicator: `vo-se-15th` is what
`vo.se` became when that account was removed and re-added while testing Manage.
Slugs are permanent once written, so the suffix stays.

## "Edit format…" opened a terminal that closed instantly

The panel's button spawned `ccs format <slug>` — and with no format argument
that command *shows* the format and returns 0. kitty appeared and vanished in
the same frame, so the button read as broken rather than as a command that had
already finished.

The panel deliberately cannot ask for free text (one click, one Action, then
close), so the terminal is the right door; it just had nothing to ask. `ccs
format <slug> --edit` is the interactive half: the token table rendered against
*this* account first — the tokens are not guessable, and it doubles as the only
preview of a colour already set — then a readline prompt seeded with the current
format, because a format string is far more often a small edit to a long line
than a new one. Blank or Ctrl-D leaves it alone.

The one pause in it is on an unknown token only. The panel's terminal closes on
return, so a warning printed there is a warning nobody reads — and pausing a run
the user typed themselves would be noise.

## The panel's 360 ms of nothing after the click

Two costs, measured 2026-07-26 by timestamping every stage from process start to
the panel's `present()`. Neither was in the widget tree, which is where the time
looked like it was going.

**The pointer probe waited out its timeout.** `PROBE_MS` is 120, and the code
called `then()` from the timeout — but `wl_pointer.enter` arrives about 5 ms
after the probes map, so 115 of those milliseconds were spent sitting on an
answer that was already in hand. The probe now resolves on the first `enter`,
and the timeout is what it should always have been: the fallback for a pointer
that is over none of the probes. Whichever fires first wins, via `_once`.

Destroying the probes moved to a `GLib.idle_add` at the same time. `finish` now
runs from inside a probe's own motion controller, and tearing down the widget
whose event is being dispatched is a different bug waiting to happen.

**GSK's default renderer costs 180 ms on the first surface.** The first
`present()` of the process took 135–180 ms while every subsequent one took 4.
That is renderer initialisation, not layout: `GSK_RENDERER=cairo` measured 2 ms
for the same window, `gl` 75, `vulkan` (the default here) 180. The panel is a
static widget tree on one 900×620 surface over a transparent scrim — there is
nothing for a GPU renderer to be faster at, and the whole init is paid on a
window that lives a few seconds. `show()` sets `GSK_RENDERER` with `setdefault`,
next to the RTLD_GLOBAL load and for the same reason: after `import gi` is too
late.

Click to panel went from ~360 ms to ~148 ms. What is left is Python starting
(8), importing ccas (32), `build_state` (14), importing gi and GTK (63), and the
window itself (30) — no single one worth chasing.

`CCAS_PANEL_OUTPUT` now skips the probe outright rather than probing and letting
`_pin_to_output` discard the answer. That is the path an agent drives the panel
from, and it was already documented as skipping it.

## One widget per monitor, and a panel that closed instead of moving

Reported 2026-07-26: *"clicking my old account widget on the left monitor and
then on the right, alternately — sometimes the previous one just closes and the
new one doesn't open"*, and separately, *"sometimes it still focuses the wrong
monitor, the one where a window is focused instead of the one where I clicked"*.

Three causes, none of them the one the report first suggested. The bar config
CCAS writes has no `"output"`, so **Waybar draws every module on every bar**:
one account has one widget per monitor, and the two complaints turn out to be
the same click seen from two sides.

**The toggle compared slugs.** `cmd_panel` closed whatever panel was open and
stopped there if the slug matched the one it was asked for — which is right for
a second click on the same widget and wrong for the *copy of that widget on the
other bar*, where the slug is identical and the intent is the opposite. Same
widget now means the account **and** the monitor, so the lock carries the
connector the panel opened on and the comparison is against the pair.

That is also why the close moved *inside* the gate. The monitor a click came
from is not known until the pointer probe answers, which happens inside the
panel process, after `panel_ui.show()` has started GTK — so the decision cannot
be taken before it. A panel that is still alive costs the probe nothing
(measured: 29 ms to answer with another panel up on the same output).

**`Gtk.Application` with an `application_id` is single-instance**, and that is
the whole of "the old one closed and no new one opened". While the previous
panel process still holds the id, `app.run()` does not start a main loop at all:
it forwards `activate` to the process that has it and returns. The new process
then built nothing, returned None as if it had been cancelled, and released the
lock on the way out — while the *old* panel quietly re-ran its activate handler.
One pid logged five probes, one per click, and not one of those clicks produced
a panel. It was always a race — SIGTERM does not make a process gone by the time
the next one reaches GTK — and closing after the probe makes the overlap normal
rather than exceptional. `Gio.ApplicationFlags.NON_UNIQUE` fixes it; note the
flag lives on `Gio`, not `Gtk`, in GTK4, and reaching for `Gtk.ApplicationFlags`
raises `AttributeError` outside the import guard, so the panel simply never
appears and nothing says why.

**The probe's timeout was shorter than a mouse click.** Waybar spawns `on-click`
on button *press*, and sway holds an implicit pointer grab until the button
comes up — during which a surface mapping under the cursor is told nothing at
all. So a click held longer than `PROBE_MS` got no `enter`, fell through to
`current_output()`, and opened on the monitor holding the focused *window*,
which under `focus_follows_mouse no` is reliably not the one that was clicked.
Measured: a 1.2 s hold on the DP-1 widget with focus on HDMI-A-1 timed out at
120 ms and opened on HDMI-A-1; the `enter` arrived the instant the button came
up. The grab always ends, so the fix is to outlast it — `PROBE_MS` is 1500, and
nothing normal pays it because `enter` still resolves the probe in about 5 ms.
It also covers a slower case that was already there: a pointer over an ordinary
window took 1123 ms to produce an `enter`.

A chip click reopens with the connector it already has, and skips the probe
entirely. The surface that has just gone away is the panel's own, on the output
a second probe would be asking about.

### What could not be measured, and why it is written down anyway

Whether killing the panel *before* probing blinds the probe on that output could
not be settled. On DP-1 — the output the user was on — a kill 200 ms before a
probe changed nothing (31 ms to answer). On HDMI-A-1 it failed four times out of
four, but so, eventually, did the baseline with nothing open and nothing killed:
the synthetic pointer stops working on an output the user is not using, which
`CLAUDE.md` already warned about and which cost half a day once before. The
reordering stands on its own — the toggle needs the connector before it can
decide — and the ambiguity is recorded here rather than dressed up as a result.

## A test enabled a real systemd timer

Found while building the usage poll (2026-07-27). The plan added
`CCAS_SYSTEMD_DIR` and `CCAS_SKIP_SYSTEMD=1` to the three *new* install tests and
stopped there. But `install.sh` writes the units unconditionally, and four tests
that predate the feature also run `install.sh` — with a sandboxed `CCAS_HOME`,
`CCAS_BIN_DIR` and the rest, but nothing at all pointing `$HOME/.config/systemd/user`
somewhere harmless. So the first green test run wrote both units into the real
systemd directory and ran `systemctl --user enable --now ccas-poll.timer`.

The suite passed. The symptom was a `ccs poll` appearing in `journalctl` every
five minutes, against the user's real accounts, from a unit whose `ExecStart`
pointed into a `tmp_path` that pytest had already removed.

Two things worth keeping from it. The first is that the env-override discipline
has a hole where the shell is concerned: every *Python* path goes through
`paths.py` and every one of those is overridable, which is easy to check. The
install scripts read their own environment directly, so a new file the installer
writes is a new place to leak, and it will not show up in any audit of
`paths.py`. `_sandbox_env` now carries both variables for every test that runs
`install.sh`, rather than each test remembering.

The second is that a green suite proved nothing here. The leak was invisible to
the tests by construction — they assert on `tmp_path`, and the escape went
somewhere they never look. It was found by running `systemctl --user list-timers`
by hand afterwards, which is the same habit `ccs doctor` exists to automate.

## A blank screen is not a bar that failed to repaint

Also 2026-07-27, verifying the poll end to end. The plan's last step asks for
`grim` before and after a poll that changed a number. Both screenshots were
identical — and the reason was not the feature. `grim -o HDMI-A-1` returned
2073600 pixels of a single colour, pure black, and so did DP-1. **The session
was locked**: the user was out and sway's lock screen was up, so `grim` captured
the lock surface covering every output and there was no bar in either frame to
differ.

Worth writing down because the first reading of "the two screenshots match" is
"the bar did not repaint", which would have been a bug report about working
code. The null case — is there *anything* on this screen — costs one call and
settles it. `getcolors()` returning a list of length one is the whole test, and
it is the first thing to run whenever a `grim` comparison comes back empty:
`swaymsg -t get_outputs` will happily report both outputs `active` with
`dpms: true` while a lock surface hides everything, so the output list does not
answer this question and the pixels do.

Note also that `CLAUDE.md`'s "Waybar is on HDMI-A-1 (x 2560–4480)" is now only
half right: HDMI-A-1 still starts at x 2560 but is 1920 wide, not 1920 tall at
2560 wide as the old note implies. The monitor geometry moved at some point.

The pixels were captured later the same morning, once the displays were awake,
and they say what they were supposed to say. A reading planted at 40 % renders
`09:30` dim with `60%` beside it; the poll then writes the true 96 % and the
widget becomes `09:29` in red with `4%`. Both cc widgets' columns move, which is
not a second repaint: the vsed label got shorter, and `modules-center` re-centres
the group around it.

**Timing is what separates the signal from the 30-second tick**, and it is worth
measuring rather than assuming, because both produce the same final pixels. If
the interval were doing the work the delay would be spread over 0–30 s. Measured
across three trials, the widget changed 0.16 s, 0.16 s and 0.15 s after `ccs
poll` returned. Three consecutive delays under 0.2 s by chance is about one in
three million, so the signal is the cause.

What stood in for the screenshot while the screens were dark, and is still the
better evidence for the *negative* half:

- A `pkill` shim early on `PATH`, which records what `ccs poll` signals. A poll
  that changed both accounts logged `-RTMIN+1` and `-RTMIN+2`, the two module
  signal numbers; the same command run again immediately logged nothing at all,
  because both readings were then unchanged. That is write-on-change and the
  signal riding on the write, measured in both directions on the real system.
- `SigCgt` out of `/proc/<waybar>/status`, which says waybar has handlers
  installed for signals 35 and 36 — SIGRTMIN+1 and +2.

Two attempts that did *not* work are worth naming, because both look reasonable.
Watching for waybar to re-run the module's `exec` by sweeping `/proc` is far too
slow to catch a process that lives about 100 ms, and matching on the command
line catches the watcher's own shell — the `pkill -f` lesson in a new shape.
Reading `/proc/<waybar>/task/*/children` is fast enough, but the children are
caught between fork and exec, so every one of them reads as `waybar` and none
can be told from the module it belongs to.

## The colour editor's two GTK4 surprises

Both found by driving the panel on 2026-07-27, and both were things the design
had reasoned out and got wrong.

**A per-widget `Gtk.CssProvider` at `PRIORITY_APPLICATION` loses to `menu.css`.**
The preview swatch cannot use `_tint()` — a colour halfway through a drag is in
neither set `_load_tints()` built its classes from, so the class would not exist
— and it therefore carries its own provider, updated per motion. Added at
`STYLE_PROVIDER_PRIORITY_APPLICATION` it did nothing visible: `_load_css()`
installs the user's `menu.css` at `PRIORITY_USER`, which outranks it, so the
swatch kept painting the `.ccas-preview { background: #353535 }` fallback
whatever the sliders said. Sampled off a `grim` capture as `(53,53,53)` when the
account's colour was `#f5c2e7`. It goes on at `PRIORITY_USER + 1`.

The same swatch paints the *stored* colour on load, not `hs_to_hex()` of the
decomposed sliders. The sliders pin lightness at `format.LIGHTNESS` and a stored
colour may not be there yet — the palette's pink sits at 86% — so painting from
them shifts the colour merely because the editor opened. The first drag is what
snaps it, which is the whole reason `seed()` and `_commit()` are two functions.

**A `GestureClick` on a `Gtk.Scale` kills the scale's drag.** `Gtk.Scale` has no
"released" signal in GTK4, so the obvious way to commit on release is a gesture
on the scale. Adding one claims the event sequence and denies the scale's own
drag gesture: the press registers and the knob then never tracks the pointer at
all. `Gtk.EventControllerLegacy` is not the way out either — its handler is
passed a `None` event here, so `get_event_type()` raises straight into the log.

So the commit is a settle timer instead: `value-changed` repaints the preview
and rearms a `SETTLE_MS` timeout, and the write happens when the movement stops.
That keeps the rule the release was there to serve — one write per drag, never
one per motion event, with `waybar.signal()` riding on it — and gives keyboard
adjustment the same single write for free. It needs no release event, which is
the part GTK4 would not reliably give.

**A synthetic pointer still cannot drag on an idle output.** The press reaches a
layer surface on the unfocused monitor — the preview repainted, so `value-changed`
fired — but motion delivered while the button is held does not move the knob.
That is the `swaymsg cursor` limit already documented under Working style, in a
new shape: a press is enough to verify a commit path end to end (click the
trough at a new position and the value jumps), a drag is not.


## The hover that went missing when an account came back (2026-07-27)

An account was removed and added again to test the flow. It worked — the widget
appeared, the click opened the panel — but it no longer lit up under the
pointer, while the other account's did.

`style.css` was the cause, and it was CCAS's stance that made it one. The
stylesheet was documented as entirely the user's, so the per-slug spacing and
hover rules were hand-written from `docs/waybar-setup.md`. GTK CSS has no
prefix matching — `#custom-cc-*` is not a selector — so those rules name every
slug one by one, and the account came back as `vo-sedlacek` where the rule still
said `vo-se-15th`. Nothing errored: a CSS selector that matches nothing is not a
mistake, so the widget kept drawing and kept clicking and simply stopped
answering the pointer. That reads as a bad install, which is how it was
reported.

Hand-keeping a list that has to be exact is the part that cannot work, so
`waybar.apply_style()` generates it now: a sentinel-fenced block written at the
**end** of `style.css`, so a stale hand-written rule above it loses on order
rather than fighting it, with everything above untouched and copied once to
`style.css.ccas-orig`. `ccs doctor` fails when a widget has no hover rule, since
the symptom is otherwise invisible until someone moves a mouse over it.

The stance did not really change — CCAS owns one region of that file and nothing
else, exactly as it does in `config.jsonc`. What changed is which region: it was
"none of it, ask the user to hand-write the part that has to match the registry",
and a thing that has to match the registry is the registry's to write.

## What `auto` cost, and why nothing migrates now (2026-07-27)

`auto` was the colour a token had when it had no colour: the usage ramp for the
windowed tokens, the account's colour for the glyph, plain for the rest. Three
behaviours under one name, which meant the only way to know what a format string
would look like was to render it. `dim` was a second way to say what the
saturation slider says by being dragged to zero. Both are gone, and a token with
no colour of its own is white — one answer, the same for every token.

They were removed rather than migrated, and so were the read-path migrations in
`registry.load()`: the integer-colour normalisation and the display-mode
rewrite. There is one user and one installation, and the accounts are re-added
rather than carried forward. A stored `auto` or `dim` fails `valid_color()` and
reads as absent, which is what the code already did with a value it did not
understand. The migration code was not free — it was a second definition of what
a registry entry means, living on the read path, and its whole job was to make
`accounts.json` mean two things at once.

Where the ramp still lives: `usage.color()`, inside `usage.bar()` and the
panel's usage bars. Pressure is shown where the numbers are, not smuggled into
the colour of a label the user picked a colour for.

## A colour control that could not tell you whether it would be seen (2026-07-27)

The panel's colour editor offered "the widget" as its first target, and the user
dragged it for ten minutes before working out that nothing was broken. The hue
slider moved, the saturation slider moved, the hex field kept up and the preview
swatch painted the colour under the pointer. Every part of the control said it
was working, and it was: the value it wrote was stored, correctly, where nothing
read it.

Their `vsed` account had a literal hex on every token in its format string. The
account's own colour reached the label through `%icon`'s `account` value — and
`%icon` had been given a hex of its own at some point, so the last path from the
widget's colour to anything on the bar had closed. A setting with no reader is
indistinguishable from a setting that works, right up until you look at the bar.

That is the complaint `auto` earned, arriving through the other door. `auto` made
a token's colour depend on *which* token asked; `account` made it depend on
whether **any** token asked. Both are ways for a colour to be a promise about
some other value rather than a colour, and neither can be read off the thing you
are editing.

`account` had also been only half-retired. Its chip was removed from the panel
earlier the same day, on the user's request, while the value stayed legal in the
registry and stayed what `registry.add()` wrote into every new account. A UI that
stops offering what the data model keeps using is the worst of the two states:
the value goes on being created and goes on mattering, and the only thing that
disappeared is the way to see it.

So a token's colour is a hex or it is nothing, and the glyph stopped being a
token at the same time. `%icon` and "the widget" were two names for one colour —
you could set them to different values, and only one of them was the bar. The ✻
is drawn by `format.render()` ahead of the format string in the account's colour,
which leaves one colour with one meaning: it paints the glyph on the bar and the
dot in the panel's account list, and there is nothing left that could disagree
with it. Moving it always moves something visible.

The chips came out of the same complaint. A dropdown shows one target at a time,
so "what colour is this token" was a click and a popup away for every token but
one — and an over-broad `.ccas-color-editor dropdown label` rule greyed its
value, which is exactly how GTK draws an insensitive widget, so the control also
looked disabled. The row of chips shows every target's colour at once, in a 2 px
strip under a name that stays at full contrast. The name is not painted in its
own colour on purpose: at low saturation it would be unreadable, and at white it
would be indistinguishable from unset.

Nothing migrated. Both accounts held values that stopped being valid — a `dim`,
an `account` — and they render white, which is what `registry.load()` already
does with anything it does not recognise. The two stored format strings still
named `%icon`, which is now an unknown token and prints as its own four
characters; they were re-set by hand, as one command per account, rather than by
teaching a read path to rewrite them.

## The retired value that made every other colour unwritable (2026-07-27)

The section above ends on the right note and the wrong assumption. Both accounts
held values that had stopped being valid, they rendered white, and nothing
migrated — that was the design, and the read path implemented it exactly.

The write path did not. `registry.set_field` validated **every** value in a
`format_colors` dict, and both callers — `ccs format <slug> --color` and the
panel's settle timer — read the stored dict, change one key and hand the whole
thing back. So `vo-sedlacek`, still holding `account` for `%5hquotaleft`, could
not have any *other* token's colour changed. The CLI returned 1 and printed
nothing. The panel's slider moved, previewed, settled, and wrote nothing. And
`ccs doctor` said all good throughout, because it does not read `format_colors`
at all.

That is the same complaint again, one level up. `account` was retired because a
colour control could be live, previewed and inert; validating on write built a
second one out of the retirement itself. A rule that says a stale value is
tolerated has to be one rule — tolerated when read *and* when written, or the
data model still refuses what the UI still shows.

A value that is not a hex is now dropped rather than rejected, which is the
answer `_chosen()` already gives it, and which quietly repairs the stored dict
the first time anything writes that account. The field must still be a dict, and
a bad colour the user actually types is still refused before it reaches here, by
the CLI, with rc 1. Nothing but a hex has ever been stored, so the guarantee that
keeps the format string layout rather than markup is untouched.

It was found by reading the user's live `accounts.json` while reviewing the
change that caused it — the value was in there, in exactly the account whose
panel had been used to demonstrate the feature working. It did not show up in
that demonstration because the token that was dragged was the poisoned one, and
overwriting the bad value with a good one is the single case that succeeds.

## A drag thrown away by the click that followed it (2026-07-27)

The colour editor writes on a settle timer: 300 ms after the last `value-changed`
with no further motion, because a `Gtk.Scale` in GTK4 has no released signal and
a `GestureClick` on one denies the scale its own drag. Picking a target calls
`seed()`, which moves both sliders to the stored colour without committing —
`on_value` cancels the pending timer and the seeding flag stops it being rearmed,
which is what keeps "I am looking at this token" from writing the registry.

Between those two is a 300 ms hole. Finish a drag, reach for another chip inside
the window, and the timer is cancelled by the seed and never rearmed: the colour
was previewed on the strip, on the swatch and in the hex field, and never
written. No error, because nothing failed.

The code is unchanged from the dropdown it replaced, and the bug is almost
certainly as old — but it needed a popup and two clicks to reach, which is longer
than the window. A chip sits an inch from the slider and takes one. Making a
control easier to reach makes its races easier to reach too.

`flush()` commits a pending drag before the target changes, against the target it
was made on, which is why `_commit` now takes an explicit one rather than reading
`target()`. Picking a chip with nothing pending still writes nothing at all.

Found by building the editor in a test harness and emitting the signals a pointer
would emit, rather than by clicking it: the module had no test file, on the
grounds that it decides nothing, and this is the part of it that decides. It has
one now — no window is mapped, so it needs a display but not the user's screen.

## The label an idle account loses at its reset (2026-07-27)

The user's second account was showing `✻ 14:50 100%` at 14:14 — its 5-hour
window exhausted, clearing at 14:50. At 14:50:32 the poller wrote a payload with
`"five_hour": null`, and the label collapsed to a bare glyph. The panel's 5h row
read **no data**. The format string was `%5hreset %5hquotaleft`, untouched, and
`ccs doctor` was green on every check.

Two facts had been sharing one state. `usage.state()` returned `ABSENT` both for
*no reading* and for *a fresh reading naming no window*, and every caller treated
that as ignorance: the tokens rendered nothing, `ccs usage` printed `5h —`, the
panel said "no data". But the endpoint drops the 5-hour window for an account
that has not been using it, so the second case is a measurement, and it says the
opposite of nothing-known — the quota refilled. It arrives exactly when a window
rolls over, which is precisely when an account is idle, so the cost was the
label of every account not currently in use.

`OPEN` — a reading whose reset had passed — was the same fact learned a different
way, and was rendered nearly as badly: no clock, and the percentage from *before*
the reset, presented as "the last thing known". It was not known any more.

So `OPEN` is gone, folded into a new `IDLE` that covers both, carrying `0.0`
rather than the dead number. `ABSENT` narrowed to no reading at all, which is
where "no data" is true and where doctor's hook and timer checks are the answer.
`IDLE` carrying a real zero is what does the work: `format._percent` already
returned "" only for a `None` percent, so `%5hused` and `%5hquotaleft` render
`0%` and `100%` with no branch added, and a panel bar at zero now *means* zero —
the ambiguity `panel.usage_rows` was written to dodge only existed because
ignorance had no kind of its own.

Said as `0% used` everywhere it is said. Not "full", which reads backwards: full
of usage, or full of quota?

It was reported as "it poofed", and the first diagnosis — correct as far as it
went — was that the code had done nothing wrong and this was the design working.
That is the trap. The design was working exactly as written, and what it wrote
was the bug. The user's own screenshot, timestamped 14:14, is what pinned the
before-state against the 14:50:32 mtime; CCAS writes only on a change, so that
mtime is not when a file was touched, it is the second the fact changed.

## Four things the inline format editor was measured into (2026-07-27)

The editor itself went in as designed. Four of its details did not survive
contact with the running panel, and one of them was in this repo's own notes.

**A previewer for an account that is not there.** The plan's `format_previewer`
called `registry.index_of()` and rendered against `registry.find(...) or {}`,
with a test for the unknown slug `build_state()` already tolerates. Both halves
raise: `index_of` has no answer but `KeyError`, and `format.render()` indexes
`hide_icon`, `color` and `email` off the account directly rather than `.get`ting
them. So the tolerance had to be spelled out — an explicit blank account, and
the index only asked for when there is one to number. Tolerating a missing
account is not the same as having a dict; `{}` is a third thing, and it is the
one that tracebacks.

**A focus grab into a widget that is in no window.** The commit rebuilds the
settings subtree, which destroys the entry it came from, so `ui_state` carries a
flag and the rebuilt editor grabs the focus back. It did not: `refresh_toggles()`
builds the whole subtree and only then appends it, so `grab_focus()` ran on a
widget with no toplevel and was dropped without a word. Nothing errors, nothing
logs, and the entry simply comes back unfocused — the next keystroke goes
nowhere. The grab is on the entry's `map` now, one shot, disconnecting itself.
Measured by pixel rather than by eye: `:focus-within` is `(52,52,66)` against
`(43,43,58)` unfocused, and neither moves on its own.

**A scrollbar drawn across the chips it was scrolling.** The insert row is a
`ScrolledWindow` and it asked for **22 px** — one chip, with the 15 px scrollbar
drawn inside them. `set_min_content_height()` does nothing here: a vertical
policy of `NEVER` makes the scrolled window propagate its child's height and
ignore the min-content one, which is exactly the direction the number was meant
to constrain. Turning off overlay scrolling only changed how it was drawn over
them. The height belongs on the widget — `set_size_request(-1, 22 + 15)`.

That exposed the second half: the drawer is an **overlay on a fixed-height
card**, and the chip scroller is the only child in it that can shrink, so every
pixel the drawer was short landed on that one row. A section had been added to
the drawer without the card growing, and the symptom was not "the panel is 20 px
too short" but "the chip row is broken". `HEIGHT` is 670. The constant's own
comment already said the card is sized to its largest state; the largest state
had changed and the comment had not.

**`ps | awk '/share\/ccas\/bin/'` — this file's own kill recipe — matches
nothing.** The panel is `python3 /home/fixed/.local/bin/ccs --gui <slug>`: the
launcher's path, not the staged package's. So every kill was a silent no-op, the
old panel stayed up, and the next `ccs --gui` did exactly what it is built to do
— saw its own widget's panel already open and toggled it shut. Three rounds of
"the panel will not open", with a log holding one harmless portal warning and no
traceback, because nothing had gone wrong. The pattern to match on is `comm ==
python3` plus `bin/ccs --gui`, which keeps the rule the old recipe was written
for: name first, so a shell cannot be a candidate. A kill pattern that matches
nothing fails exactly like one that matches too much, and it fails more quietly.

**The eight-hour horizon was a horizon, not a law.** The user asked why they had
to "reset claude" about once every eight hours to get an idle account's numbers
back. Eight hours is exactly the access token's life — issued 14:37, `expiresAt`
22:37 — and their refresh tokens were healthy for another four weeks. So nothing
was invalidating anything: the token aged out on schedule and `ccs poll` refused
to renew it, by a rule written here in capitals. The rule's *reason* was sound
and is unchanged — CCAS posting to the token endpoint rotates a refresh token
behind Claude Code's back, which risks the login and can invalidate what a live
session holds — but the conclusion drawn from it was wider than the reason. "CCAS
must not rotate the token" is not "the token must not be rotated". Claude Code
renews on its way past, under its own cross-process lock and for no model quota.
The renewal is the expired path's alone — asking on every poll would spawn a
claude every five minutes for an account that needs nothing — and
`CCAS_NO_TOKEN_REFRESH=1` restores the old behaviour whole, tested by asserting
claude is *not asked*, because an off switch that only goes quieter is not one.

**And it shipped inert, because the command was assumed rather than measured.**
The first version called `accounts.auth_status()` — already in the tree, already
running claude under the account's `CLAUDE_CONFIG_DIR`, so it looked like the
renewal was free. It was reasoned into place: *`auth status` is about auth,
therefore it refreshes.* It does not. Run against an account five hours past
expiry it prints `loggedIn: true` with the email and the org, exits 0, and leaves
`.credentials.json` untouched to the mtime. A whole night of five-minute polls
said `claude did not renew it; is it logged in?` — and it *was* logged in.

Two things kept that from being worse. The renewal **verifies instead of
trusting**: `renew()` re-reads the file and answers None if the token is still
expired, so a renewal that does nothing degrades to exactly the old horizon
rather than to a poll that fetches with a dead token. And the failure message
named the account and the fix, so the journal said what had happened rather than
going quiet. The instrument was sound; the assumption inside it was not.

What actually renews is a **first-party API call** — the bundle attaches
`refreshOAuth: true` to those, which `auth status` plainly does not make. Found
by running the cheap subcommands against that still-expired account and watching
the credential mtime: `mcp list` refreshed it (8h horizon restored, refresh token
untouched, 2.5s), while `doctor`, `agents` and `project` did not. So `mcp list`
is what `RENEW_ARGS` holds — chosen by measurement, and *pinned by a test*,
because which command it is turned out to be the entire feature.

The general lesson is the one this file keeps relearning: **an integration point
that is one call away still has to be measured.** A command whose name matches
the intent is not evidence it does the thing, and "it returned 0" is not evidence
either. The check that would have caught this on day one took ninety seconds —
stat the file, run the command, stat it again — and it was skipped because there
was no expired token to hand and the reasoning felt tight. Wait for the real
condition, or build one; do not ship on the reasoning.

## The account switch that rebuilt the whole window (2026-07-27)

Clicking another account's chip closed the panel. `pick()` sent
`Action("switch", …)`, which was not in `panel.STAYS_OPEN`, so the window shut,
`show()` returned, `app.run()` unwound, and `cmd_panel`'s loop called
`panel_ui.show()` again on the next slug — a new `Gtk.Application`, a new layer
surface, a fresh `present()`, for a change of one dict. On screen it is a blink,
and everything the user had done in that window went with it.

Nothing in that teardown was load-bearing. The frame holds **exactly one child**
and every account-shaped widget is inside it; the layer-shell setup, the scrim,
the Escape controller and the size negotiation know nothing about which account
is showing. So a switch swaps that child, and the surface never unmaps.

**The lock is the half that is not in the widget tree.** The bar's toggle
compares (slug, output) against the widget clicked. A panel showing `two` behind
a lock still naming `one` makes `two`'s own widget read as a *different* widget:
it closes the panel and opens a fresh one — the reload, back through the door it
was shown out of. `dispatch_panel`'s switch branch re-claims the lock, taking
the connector from the lock this very process wrote, because there is no gate
closure to read it from and the answer is already on disk. Verified on the real
panel: switch to `vose` inside the window, then `ccs --gui vo-sedlacek` on the
same output exits 0 with no panel left and no lock.

With nothing reopening, three things stopped having a reason to exist: the loop,
the gate's `first` flag — which was there so a reopening chip click did not make
the panel `SIGTERM` itself — and `show(output=…)` / `_pointer_output(skip=…)`,
which existed to skip the pointer probe on that reopen. `CCAS_PANEL_OUTPUT`'s
own skip is a different question and stays.

**A discarded body has to be retired while it is still alive.** A `GtkListBox`
announces `row-selected` as it is disposed. A body that has been swapped out is
disposed whenever the garbage collector reaches it, and by then CPython has
cleared the closure cells its handlers were built from — so the handler runs
against a `view` that no longer holds anything and prints a `NameError`
traceback into Waybar's log, once per switch. It is reproducible on demand
(`swap(); gc.collect()`) and absent without the swap, which is what identified
it. `_retire()` unselects the rows first: the disposal then has nothing left to
announce, and the emission it does cause happens while the handlers are intact.

The same fact bit a second time, in the opposite direction. The session the user
had picked is recorded from the **row**, never from `selected_session()`'s
fallback — because `_retire`'s unselect is itself a `row-selected`, and
recording the effective session there would have replaced their choice with the
top of the list, in the moment before the new body read it back.

**And `GtkSearchEntry` arms `search-changed` on a delay.** Seeding the rebuilt
entry with the remembered query fires one about 150 ms into the new body, where
`on_search` does what a *new* search should do and drops the selected project
for the top hit — a selection quietly moving a moment after a click that had
nothing to do with it. The echo is recognised by the text being what was already
remembered.

What carries over is the drawer, the query and the selected project and session:
the panes are not per-account at all — `history.scan()` reads
`~/.claude/projects` and both accounts see the same list — so a switch that
emptied them would be discarding work over a change that cannot have affected
what was being looked for. The colour target does not: the chip row is one chip
per token in *this* account's format string, so the index names a different
token either side of a switch, and `icon` is the one target every account has.

## `fetched_at` cannot be a rate limit (2026-07-28)

The panel now asks the usage endpoint when it opens, so that the account you are
looking at is fresh rather than up to five minutes old. That needs a guard, and
the guard `poll.py` already had looked like the obvious one to reuse with a
shorter interval:

```python
def due(slug, now, interval=INTERVAL):
    reading = usage.load(slug)
    if reading is None:
        return True
    return (now - reading.get("fetched_at", 0.0)) >= interval
```

It would have engaged approximately never. `usage.record` defines `fetched_at`
as *when this reading was first seen*, and means it — the write-on-change rule
is what makes the statusline hook affordable at a few hundred milliseconds, so a
reading whose numbers are steady is simply not rewritten and its timestamp does
not move. For the timer that is exactly right: the question there is the
cadence. For a rate limit it is backwards, and worst of all it fails for the
account the feature exists to serve. An idle account is the one whose usage is
*not* moving, so its `fetched_at` is permanently older than any interval, so
`due()` is permanently true, so every panel open would have spent a request at
any setting.

Two timestamps, two facts: when the answer last changed, and when we last asked.
The second is `poll-stamp`, its own file beside `usage.json`, and it is written
**before** the fetch rather than after. Stamping on success only turns a broken
network into an unthrottled retry — one request per panel open, none of which
can succeed — which is the precise behaviour a rate limit is for.

That the endpoint is rate limited at all is not an assumption. Claude Code's own
bundle carries a `rateLimitedVia` path for `GET /api/oauth/usage`, distinguishing
an envelope-level limit from an HTTP one, and degrades `/usage` to *"Showing
last-known usage … (rate limited — try again in a moment)"* against persisted or
header-seeded numbers. `poll.fetch()` has no such fallback: a 429 is
`FAILED — reading unchanged`, which on the bar and in the panel is
indistinguishable from the staleness the poller exists to remove. The cheap
request is the one a human just asked for; the expensive one is the timer
tightened to spend them continuously whether or not anyone is looking.

## The panel's usage bars have never shown the ramp (2026-07-28)

Found while photographing the on-demand refresh. Recorded as **not fixed** at
first, on the grounds that it lives in `assets/menu.css`, which is the user's the
moment `install.sh` copies it — see the bottom of this section for why that was
the wrong read of where the fix belongs.

`_load_tints()` installs `progressbar.ccas-tint-fab387 progress { background:
#fab387; }` and friends at `STYLE_PROVIDER_PRIORITY_APPLICATION`, covering the
whole of `usage.RAMP`, and `_usage_bar()` puts the right class on the bar. But
`menu.css` is loaded at `PRIORITY_USER` and says:

```css
.ccas-usage-bar progress { min-height: 6px; border-radius: 999px;
                           background: #89b4fa; }
```

Priority beats specificity in GTK CSS, so the flat blue wins for every reading.
Seeding a reading of 88% — `usage.color(88)` is `#fab387` — and opening the panel
draws a blue bar. The class is there; it paints nothing.

This is the same trap the colour editor's preview swatch already documents, from
the other side: a per-widget provider at `PRIORITY_APPLICATION` is outranked by
anything in `menu.css`, silently, because a losing rule is not an error. The
claim in CLAUDE.md that `usage.color()` "still ramps inside `usage.bar()` and the
panel's bars" holds for the first and not the second.

Fixed the same day, in the code rather than the stylesheet. That the rule *lived*
in `menu.css` did not mean the fix did — the opposite: `install.sh` copies that
file once and never again, so a corrected `assets/menu.css` would have fixed
nobody who already had a panel, which is everyone who has one. The provider is
what moved. `_load_tints()` now adds two: the colours the *user* chose — palette
entries, account colours — stay at `PRIORITY_APPLICATION`, below `menu.css`,
because they say what a colour is and the stylesheet should stay free to override
them; `usage.RAMP` goes in at `PRIORITY_USER + 1`, above it, because a colour
that is a function of the reading is not a theme choice. That is the priority the
preview swatch already uses, for the neighbouring reason.

Verified at 85% and 60% seeded together: peach and yellow, where both had been
blue. The panel was mapped on a `swaymsg create_output` headless output and
unplugged afterwards — both real monitors were in use, and the rule about never
mapping a test surface onto one the user is working on does not have an exception
for "it is only for a second". A headless output is the escape from that rule,
and it is cheaper than every offscreen-render approach tried before it:
`Gtk.WidgetPaintable` over an unmapped widget snapshots to a `None` node however
carefully it is realized and allocated, and `Gsk.Renderer` then aborts the
process on dispose rather than raising.

What the stylesheet keeps is the resting colour below 50%, where the ramp names
nothing, and every other property in the rule — the height and the radius are
untouched by any of this. The rule now says so beside itself, because a
`background` that silently stops applying above 50% is exactly the kind of thing
that costs someone ten minutes.

## An account with a live window read 100% out of nowhere (2026-07-28)

The user was watching a video when one account's widget went to `100%`. Clicking
it opened the panel, and a second or two later it corrected itself. Nothing was
wrong with the account, nothing was stale, and every hop involved was behaving
exactly as specified.

The label was `%5hreset %5hquotaleft` — quota *left*, so `100%` means nothing
spent. That is what made it alarming rather than obviously wrong: on a usage
widget a lone round number reads as 100% *used*.

The first explanation was wrong and worth recording as a warning about the
evidence. The journal showed a genuine idle stretch that afternoon — the 5-hour
window rolled over at 12:59, and for 21 minutes the endpoint reported no running
window, which renders `100%` correctly:

```
12:47  vsed  ok  5h ≥40% clears 12:59
13:03  vsed  ok  5h 0% used            ← rolled over, endpoint names no window
13:08  vsed  unchanged  same numbers
13:24  vsed  ok  5h ≥1% clears 18:09   ← a new window started
```

That is a real state, it renders `100%`, and it was not this. The user said it
had happened ten minutes ago, not ninety, and the timer's own log ruled the
afternoon story out: at 14:33:31 the poll said `unchanged`, meaning the stored
reading matched what the endpoint had just answered — a live 5-hour window at 1%.
Two minutes later the panel's on-demand poll *wrote*, which it only does on a
change. So something had overwritten the reading in between, and the only other
writer is the statusline hook, from the session running under that account.

`_reading()` refuses a payload naming neither window — "a payload with no
windows must not erase a good one". A payload naming **one** went straight
through. A statusline tick carrying `seven_day` and no usable `five_hour` (a
missing `resets_at` is enough, `_window` returns None for it) therefore wrote
`five_hour: null` over a running window. From there every step is the documented
design: `state()` reads a null window as IDLE, IDLE carries `0.0` rather than
None *on purpose*, and `%5hquotaleft` renders `100 - 0`. Four correct rules
composing into a wrong label.

Two fixes. `record_reading` carries a window the incoming reading does not name
over from the stored one — silence about a window is not a measurement of it.
That cannot resurrect a rolled-over window, because `resets_at` is an absolute
anchor and `state()` calls a carried window whose reset has passed IDLE from the
timestamp alone; the worst a carry does is keep a true fact one tick longer than
the source mentioned it. And the clock slots (`%5hreset`, `%5htimeleft`) now
render `usage.IDLE_MARK` instead of nothing, so the idle state that *is* real —
the 13:03 one above — says `idle 100%` rather than a bare `100%`. ABSENT keeps
the empty string: there the answer is that we do not know, and "idle" would be a
claim the reading cannot support.

The lesson about the investigation is the one worth keeping. The first
explanation fitted a real mechanism, had log evidence behind it, and was still
wrong — it was checked against the wrong minute. What settled it was the pair of
writes: a poll that reported `unchanged`, then a poll that wrote. Write-on-change
turns the log into a record of when the reading moved, which is a stronger
instrument than any single line in it.

## The reset clock that flipped a minute on every poll (2026-07-28)

Reported as a regression in the other direction from the one above: the
percentage on the bar *used* to move often, and now looked as though only the
five-minute timer touched it. The hook was suspected of being ignored — the
carry added the same afternoon is exactly the sort of change that would do it.

It was not. Instrumenting the live `ccs statusline` (log the payload, the stored
reading, and whether `record` wrote) showed the hook working end to end: 32
payloads in four minutes, every one naming **both** windows — so the carry never
fired at all — a write on each 1% step, and a `render` in Waybar's log 50 ms
after each `waybar.signal()`. The ceiling is Claude Code's own granularity: its
`rate_limits` moves in whole percent, about once a minute under a working agent,
and an integer percent cannot be repainted more often than it changes.

What *did* change on the five-minute boundary was the clock beside it. The
poll's log had been saying it for hours and it read as noise:

```
15:21:01 vo-sedlacek ok 5h ≥55% clears 18:39
15:41:31 vo-sedlacek ok 5h ≥56% clears 18:40
15:47:00 vo-sedlacek ok 5h ≥56% clears 18:39
```

Three forced polls twenty seconds apart, logging the raw body, gave the cause.
One window, one anchor, three answers:

```
2026-07-28T16:09:59.820816+00:00
2026-07-28T16:10:00.488736+00:00
2026-07-28T16:09:59.956668+00:00
```

The endpoint's `resets_at` jitters either side of the minute it means, and
`_epoch()` truncated the fraction — so the same anchor quantised to two
different seconds, and therefore to two different *minutes*. `%5hreset` flipped
18:09 ↔ 18:10 on every poll; each flip was a change `record_reading` had to
write and `waybar.signal()` had to repaint, for a window that had not moved, and
half of them named the wrong minute. It also put the two sources permanently at
odds: the statusline hands the same window a whole epoch **on** the minute
(`1785255000`), so hook and poll overwrote each other in turn, every time.

`_epoch()` now rounds to the minute, which is where a reset anchor actually
lands — all four statusline epochs seen are divisible by 60 and every ISO string
measured is within a second of a minute boundary. Two forced polls after the fix
say `unchanged  same numbers`.

The user's report was accurate about the symptom and wrong about the mechanism,
which is the normal case: they saw the widget change on the timer's cadence, and
that was true — it was the clock digit, not the percentage. Worth keeping is
that the instrument answered a question nobody had asked. The measurement built
to prove the hook still fires is what put the poll's own log side by side with
the raw body, and the flip had been in that log all day reading as a rounding
quirk of no consequence.

## The session that dies while the installer runs (2026-07-28)

Reported as "my session randomly crashes while you're working". Three of them
are in the transcripts — 07-25 23:29:54, 07-27 05:04:39, 07-28 16:00:25 — and
the third happened mid-investigation, which is what made it findable.

What a crash looks like from the outside: the transcript stops mid-turn, with a
tool result written and no turn ever built on it, and resumes minutes later on
the harness's `Continue from where you left off.` The signature is worth writing
down, because the obvious search finds the wrong thing. Sessions that end at a
turn boundary are `/clear`; sessions with no closing `turn_duration` are mostly
*live*; and a crash leaves **no new file at all**, because the resume continues
the same one. It is a mid-turn gap *inside* a transcript, not an abrupt end of
one. Of 149 "abrupt endings" in two days, exactly three were crashes.

What it is not, all measured rather than assumed: no coredump (`coredumpctl`
has python's aborts from the panel and nothing from node), no OOM and no kernel
line at all, no systemd action on the scope, and the scope's own accounting says
447 MB peak. The MCP log settles what kind of death it was — 30 ms *before* the
last tool result was recorded, the CLI sent SIGINT to its MCP server and reaped
it cleanly, and the session's pid file under `~/.claude/sessions/` was removed.
That is an orderly shutdown, not a fault: something asked the process to exit.

Two of the three land in the same second as `./install.sh`. The 07-25 one is
the same command with `&& ccs doctor` on the end. And the session that died is
the only one that died: two other live sessions ran straight through 16:00:25.
The asymmetry between them is visible in `ps`. A session launched from the panel
descends from the bar —

```
claude ← kitty ← python3 ccs --gui vo-sedlacek ← waybar
```

— with kitty in the panel's own process group, while a terminal-launched
session has kitty at PPID 1 and is reachable from none of it. `install.sh` ends
with `ccs config`, which sends Waybar a SIGUSR2.

That is where the evidence stops and a hypothesis would start, so it is left as
one. What the investigation *did* prove is a defect of our own on the same path,
and it is fixed here: `install.sh` replaced both the staged package and the
launcher **in place**, while a live session spawns `ccs` through its statusline
every few hundred milliseconds. Measured with a sub-millisecond poll across ten
installs: 1.2 ms per install with the package directory absent — or worse,
present and half-extracted, `ccas/__init__.py` there and `ccas/cli.py` not — and
0.2 ms with the launcher truncated to zero bytes by `cat >`. The package is now
extracted into a staging directory and swapped in, and the launcher is written
beside itself and renamed over, so a spawn gets the whole old file or the whole
new one. The same measurement after: the launcher window is gone entirely and
the package window is 0.29 ms, one rename rather than one tar. The residual is
the two renames of a directory swap; closing it completely would mean a
versioned package directory behind a symlink, which is not worth the layout.

At roughly one spawn per 300 ms that window is about 0.1% per install per live
session, so it is not on its own an explanation for two crashes — it is one
mechanism removed rather than the cause identified. The honest state: the shape
of the death is known, the trigger correlates with the installer twice out of
three, and the next occurrence needs a signal trace to name the sender.
