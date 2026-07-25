# Spike: Waybar `menu-file` capabilities

**Date:** 2026-07-25
**Waybar version:** 0.15.0
**Resolves:** spec §11 "Open risk"
**Method:** a temporary `custom/spike` module in `~/.config/waybar/config.jsonc`
pointing at a hand-written `menu.xml` with one top-level item (`alpha`) and one
`GtkMenuItem` carrying a `<child type="submenu">` containing `nested`. Both were
wired to `notify-send` through `menu-actions`. The label of `alpha` carried a
generation marker edited in place between clicks. All observations are the
user's, reported live.

## Findings

### 1. Nested submenus render — **supported**

`Parent ▸` opened a submenu containing `Nested item`. GtkBuilder's
`<child type="submenu"><object class="GtkMenu">` nesting is honoured by Waybar's
`menu-file` loader.

### 2. `menu-actions` addresses items inside submenus — **supported**

Clicking `Nested item` produced the `ccs-spike nested` notification. Action IDs
are looked up across the whole widget tree, not just the root menu's direct
children, so a nested item needs no special addressing.

### 3. `menu-file` is **cached at startup**, not re-read per popup

With Waybar untouched, `Alpha GENERATION-1` was edited on disk to
`GENERATION-2`. Re-opening the menu still showed **`GENERATION-1`**. Waybar
parses the descriptor once when the module is constructed and reuses the built
widget for every popup.

### 4. `SIGUSR2` reload **does** pick up a changed `menu-file`

After `killall -SIGUSR2 waybar`, the menu showed `GENERATION-2` — the edit made
in finding 3. A full reload reconstructs the module and re-parses the file.

### 5. Per-module `SIGRTMIN+n` **does not** re-read `menu-file`

With the module given `exec` / `interval` / `signal: 9`, the file was edited to
`GENERATION-3` and `pkill -RTMIN+9 waybar` sent. The menu still showed
**`GENERATION-2`**. The realtime signal re-runs `exec` and updates the label
only; it does not touch the cached menu widget.

This is the one that costs us. The flicker-free refresh path works for labels
but is unavailable for menu content.

## Consequences for the implementation

| Area | Decision |
|---|---|
| Menu structure | Build the **nested** menu exactly as spec §6 describes. The flat fallback is not needed. |
| History freshness | The 30 s `exec` regeneration of `menu.xml` is **not** sufficient on its own — Waybar will keep serving the menu it parsed at startup. |
| Refresh trigger | `ccas.menu.write()` must call `waybar.reload()` (`killall -SIGUSR2 waybar`) after writing, **but only when the session set actually changed** — `menu.session_set_changed(slug, sessions)` compares row count and newest uuid against the existing `history.tsv`. Unconditional reloading would flicker the bar every 30 seconds. |
| Colour / display / hide changes | Unaffected. These are `exec` output, so `waybar.signal(n)` remains the correct, flicker-free path (spec §4's update table stands for label changes). |

The design holds as written; only the menu refresh trigger changes, exactly as
spec §11 anticipated under its "Caches" branch.

## Cleanup

The `custom/spike` module and its `modules-right` entry were removed from
`config.jsonc` and Waybar reloaded. `~/.config/waybar/config.jsonc.pre-spike`
holds the pre-spike copy.
