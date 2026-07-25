# Retiring `menu.xml`: one picker, no cached menu

2026-07-25.

The bar reloads itself when a new Claude session appears. This removes the
reason it has to: Waybar's `menu-file` goes away, a left click runs the fuzzel
screen that already exists (`cli.cmd_mode_menu`), and nothing about the menu is
cached any more, so nothing about the menu needs invalidating.

## The observation

Start a session, send the first prompt, and within 30 s the bar blinks. That is
working as designed: the first prompt is what creates the session's jsonl under
`~/.claude/projects`, the next `ccs render <slug>` tick sees a uuid that is not
in `history.tsv`, `menu.session_set_changed()` returns True, and `cmd_render`
rewrites the pair and calls `killall -SIGUSR2 waybar`. Once per new session,
arriving unannounced while the user is typing.

## Why it cannot be deferred to the moment the menu opens

The spike measured all three halves of this (`docs/superpowers/spike-waybar-menu.md`):

- **Finding 3** — `menu-file` is parsed once when the module is constructed and
  the built widget is reused for every popup. Editing the file changes nothing.
- **Finding 4** — only `SIGUSR2` re-parses it, and that reconstructs the module.
- **Finding 5** — the per-module `SIGRTMIN+n` re-runs `exec` and repaints the
  label only.

There is no menu-open hook. A refresh at reveal time would have to be the full
reload, which tears down and rebuilds the very module the popup belongs to — the
menu would vanish as it opened. So "refresh exactly when the dropdown is
revealed" is not a thing that can be built on `menu-file`.

Everything in the *manage it* family — debounce, coalesce, reload only when the
list is badly stale, reload only when the user looks idle — moves the flicker to
a different minute without removing it, and buys that with a scheduler nobody
can test deterministically. Recorded so it is not re-litigated: the flicker is a
property of caching a menu we cannot invalidate, not of when we choose to
invalidate it.

## The decision

Delete the cache. `menu`, `menu-file` and `menu-actions` come out of the module
block; `on-click` runs the account's picker, which is generated fresh on every
click and therefore cannot be stale.

This costs almost nothing to build because the screen already exists.
`cli.cmd_mode_menu()` is the fzf/fuzzel version of the Waybar menu, and
`ccs --gui <slug>` already reaches it through `main()`'s account-slug branch.
The GtkMenu is a second implementation of a screen CCAS already has; the two
have been drifting (the menu has Colour and Display, the picker has neither),
and this converges them onto the one that is not cached.

## What changes

### The module block

```python
{
    "exec": f"{ccs} render {slug}",
    "interval": 30,
    "signal": account["signal"],
    "tooltip": False,
    "on-click": f"{ccs} {slug}",          # `ccs --gui <slug>` — see below
}
```

`_ccs()` already appends `--gui`, so the click keeps carrying the flag that
`pickers.is_gui()` cannot be trusted to infer. `test_generated_commands_all_
force_gui_mode` moves from the `menu-actions` map to `on-click`; the pair with
`test_passthrough_does_not_force_gui_mode` stands unchanged.

### Three rows move into the picker

`cmd_mode_menu` gains `Display as…`, `Hide icon` and `Color…`, in the GtkMenu's
grouping order: launch verbs, then appearance, then the two runner toggles, then
`Manage…`. `Display as…` and `Color…` open a second prompt in the style of
`Manage…`; `Hide icon` is a mark row that toggles in place, like `headless` and
`dangerous` beside it. All three use `MARK_ON`/`MARK_OFF`, the same as today.

**The launch verbs are not shared, and this section was read as saying they
were.** Corrected 2026-07-26, after the merged screen shipped with the
terminal's rows on the bar. `cmd_mode_menu` scopes them to `os.getcwd()`, which
is the user's directory from a terminal and *Waybar's* from a click, so under
`gui` the GtkMenu's directory-free three — `New session`, `Resume last session`,
`Resume from history…` — stand in for `New here (…)`, `Resume last in …`,
`History in ……` and `All projects…`, and `launch.run` is passed `cwd=None`.
Everything below the verbs is identical from both doors. `docs/why.md` has what
the wrong version did.

The picker's prompt becomes `label.display_name(account)` — the identity that
was the menu's title row. Considered and rejected: passing the email through
fuzzel's `--mesg`, which would mean a new parameter on `pickers.choose()` for a
line the prompt already carries.

### History stops being a snapshot

`launch._rows()` reads `history.scan()` live instead of `menu.read_tsv()`.

The alignment invariant that forced the coupling — visible row *i* ↔ `hist-i` ↔
line *i* of `history.tsv`, and therefore "write and reload are one operation" —
exists because a GtkMenu item can only carry a fixed action id. With the rows
generated per click there is no id, no index and no snapshot, so the invariant
has nothing left to protect. `launch.resolve`'s `hist` mode goes with it.

`paths.MENU_HIST_ITEMS` (15) was a GtkMenu constraint: 219 items filled a
1440 px screen and a GtkMenu does not scroll usefully. fuzzel scrolls, so the
cap is deleted and `HIST_SLOTS` (300) alone bounds the list.

### What still reloads — and it is genuinely required

Only `waybar.apply()` when the set of modules changes: `cmd_add` and `cmd_rm`.
That rewrites `config.jsonc`, which Waybar reads at startup, so the reload is
the point rather than a side effect.

Everything else drops to the flicker-free path:

| change | today | after |
|---|---|---|
| nickname, colour, display, hide | full reload (`_refresh`) | `waybar.signal(n)` |
| headless, dangerous | full reload (`_refresh_all`) | nothing — neither is in the label |
| a new session appears | full reload (`cmd_render`) | nothing |
| add, remove | full reload | full reload |

`_refresh` reloads today only because the ●/○ marks were baked into the cached
XML. With no XML, a signal is enough — which is what the spike said the signal
was for. `_refresh_all` exists to rewrite *every* menu when the exclusive
`headless` mark moves; with no menus to rewrite it collapses to
`registry.save()`. The invisible-icon warning stays in `_refresh`.

`cmd_render` becomes a pure label print: no write, no reload, no
`session_set_changed`. `ccs config` keeps its job — rewrite the managed block,
strip the old bashrc function, reload — minus the menu rebuild loop, and stays
what `install.sh` runs.

### Deletions

- `menu.py`: `build_xml`, `build_tsv`, `write`, `read_tsv`, `session_set_changed`,
  `_item`, `_title`, `_submenu`, `_separator`, `_atomic_write`. What survives is
  `MARK_ON`/`MARK_OFF`, which move to `label.py` — they are how state is drawn,
  and every remaining caller is a picker row. `menu.py` and `tests/test_menu.py`
  are removed.
- `waybar.module_config`: the whole `actions` map.
- `doctor.py:77`: the `menu.xml` / `history.tsv` presence check. Neither file
  exists any more, and nothing replaces it — doctor keeps the symlink, registry
  and `config.jsonc` checks, which are the places that still drift.
- `paths.MENU_HIST_ITEMS`.

Out of scope, deliberately: the `interval: 30` tick stays. The label only
changes when the registry changes and every one of those paths now signals, so
`"interval": "once"` would probably do — but that is a separate change with its
own thing to verify on the real bar, and it is not what this one is for.

### Rules in CLAUDE.md that retire with the cache

Four of the hard invariants exist only to protect `menu.xml`, and the
implementation must strike them or they will outlive what they describe:
"Point Waybar at a menu only after writing it"; "Write and reload are one
operation"; "`ccs render` no longer forces a rebuild"; and
"`session_set_changed()` is set-based on purpose". The Waybar caching facts
above them stay — they are why the cache is being deleted, and the next person
to reach for `menu-file` needs them.

Existing account directories keep a stale `menu.xml` and `history.tsv`.
`uninstall.sh` already trashes the account directory under `--purge`; the files
are inert otherwise and are not worth a migration.

## Placement

No positioning flags. The picker appears wherever the user's fuzzel config puts
it, exactly like the account picker and the project picker do today. Anchoring
it under the bar was considered and rejected: it would hardcode a position that
only makes sense on `HDMI-A-1`, and it would make CCAS own placement it does not
own today — the same line already drawn around `style.css`.

## Accepted losses

1. **The popup is no longer anchored at the pointer.** A GtkMenu opens where you
   clicked; fuzzel opens where fuzzel opens. Accepted deliberately, above.
2. **Mouse-only navigation is not lost.** Measured on 2026-07-26, once the
   screen was unlocked: pointing at a row highlights it and a left click selects
   it and exits. The click was aimed at row *three* of five deliberately, so a
   stray Enter — which commits the highlighted first row — could not be mistaken
   for a pass; fuzzel printed `ROW-THREE`. So this is not the regression it was
   written up as, and `docs/why.md` gets nothing.

   The rows are confirmed twice over. Without pixels: a stub `fuzzel` on `PATH`
   in front of the installed `ccs --gui vsed` captured all ten rows, the prompt
   as the account's nickname, and the `Display as…` sub-prompt with the current
   mode marked ●. With pixels: `grim` on the live picker shows `○` and `●`
   rendering as a hollow ring and a filled dot of the same size — the pair
   survives fuzzel's font stack as well as the bar's.
3. **Colour and display are one prompt deeper** than a hover submenu. In exchange
   they become type-to-filter, and reachable from a terminal for the first time.

## Testing

`tests/test_menu.py` goes. Its behaviour splits: mark rendering follows
`MARK_ON`/`MARK_OFF` into `tests/test_label.py`, and the row content of the
picker is `tests/test_cli.py`'s, which already owns `cmd_mode_menu`.
`tests/test_launch.py` loses the `hist` mode and gains live-scan resolution for
`last` and the history list. `tests/test_waybar.py` asserts the module block has
no `menu*` keys and that `on-click` carries `--gui`.

The invariants that do not move: nothing writes to `~/.claude`
(`test_relink_never_touches_mtimes_in_claude_home` is still the canary), every
path is `CCAS_*`-overridable, and `ccs doctor` still never writes.
