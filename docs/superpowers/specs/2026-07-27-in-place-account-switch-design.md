# Switching account without rebuilding the window

The panel's header carries one chip per account. Clicking another account's chip
emits `panel.Action("switch", slug, None)`, which is not in `panel.STAYS_OPEN`
— so `pick()` closes the window, `show()` returns, `app.run()` unwinds, and
`cmd_panel`'s loop calls `panel_ui.show()` again with the other slug. A new
`Gtk.Application`, a new layer surface, a new `present()`.

Nothing in that teardown is needed. The account is one dict away, and every
widget that renders it lives inside a single child of the panel's frame: `_open`
builds `frame` and appends exactly one thing to it, `build_body(state, …)`. The
layer-shell setup, the scrim, the Escape controller and the surface itself know
nothing about which account is showing.

So a switch swaps that child. The surface never unmaps and nothing blinks.

## What changes

**`"switch"` joins `panel.STAYS_OPEN`.** That set was documented as "a setting,
not a departure", and a switch was a departure because it handed the screen to
another panel. It no longer hands the screen anywhere — it is the same window
showing another account, which is exactly what the set now means: the kinds the
panel applies where they were clicked instead of returning them.

**`dispatch_panel`'s switch branch re-claims the lock.** `panel.claim(slug,
output)` writes pid, slug and connector, and the bar's toggle compares the pair
against the widget that was clicked. A panel showing `two` behind a lock that
still says `one` makes `two`'s own widget read as a *different* widget: it
would close the panel and open a fresh one, which is the reload this removes,
reintroduced by the back door. The connector comes from `panel.running_panel()`
— the lock this very process wrote — because `dispatch_panel` has no closure
over the gate and the answer is already on disk. The `cli.SWITCH` sentinel goes.

**`panel_ui` grows `_body_swapper`.** A factory returning `rebuild(slug)`:
clear the frame, build `panel.build_state(slug)`, append a fresh `build_body`.
It returns False so it can be handed straight to `GLib.idle_add` — and it must
be, for the reason `refresh_toggles` already is: the widget being destroyed is
the one whose `clicked` handler is running.

`pick()` routes a switch through `apply` and then the swapper. It stays inside
the `STAYS_OPEN` branch, so a panel opened with no `apply` (the default) still
returns the action and closes, which is what `show()`'s contract says.

**`cmd_panel` loses its loop**, and with it the gate's `first` flag — the gate
is now called exactly once per panel, so the branch that existed for a chip
reopening through it is dead. `show(output=…)` and `_pointer_output(skip=…)` go
the same way: they exist only to skip the pointer probe on a reopen that no
longer happens. `CCAS_PANEL_OUTPUT`'s own skip, `_skip_probe()`, is untouched —
that is a different question with a different answer.

**`_load_tints` covers the whole ramp** instead of the current account's two
usage colours. The tints are a display-level CSS provider built once, before the
first surface maps; a switch would otherwise need a second provider for a colour
the new account's reading happens to reach. `usage.color()` answers a value from
`usage.RAMP` or None, so naming the ramp is a superset of what any account can
ask for, and every account's colour is already in `state["accounts"]` whichever
one is current. No provider is added after the panel opens.

## What carries over

The body is rebuilt, so anything held in its closures would be lost. `ui_state`
already exists for exactly this reason — `refresh_toggles` destroys the settings
subtree on every applied setting — and it moves up to `_open`, where it survives
a swap as well:

- **the drawer's open state.** The chips sit in the header, which stays visible
  while the drawer is out, so switching account mid-edit is a reachable click.
  Having the drawer slam shut is the same complaint that put the settings in
  place rather than behind a reopen.
- **the search text, and the selected project and session.** The panes are not
  per-account at all: `history.scan()` reads `~/.claude/projects`, and both
  accounts see the same list. A switch that emptied a typed filter would be
  discarding work for no reason — the thing being searched has not changed.
- **not the colour target.** It resets to `icon`. The chip row is one chip per
  token in *this* account's format string, so index 2 means a different token
  either side of a switch; the selection is visible on screen, so it would not
  be wrong, only arbitrary. `icon` is the one target every account has.

## Tests

- `panel.STAYS_OPEN` contains `switch`; the launch and terminal kinds still do
  not (`test_panel.py`).
- `dispatch_panel("switch")` leaves the lock naming the new slug on the same
  connector, and returns 0 (`test_cli.py`).
- `cmd_panel` calls `panel_ui.show` exactly once, and the gate exactly once
  (`test_cli.py`, replacing the three tests that pinned the reopen).
- A chip click calls `apply` with the switch action, asks the swapper for the
  new slug, and does not close the window (`test_panel_ui.py`).
- The swapper leaves the frame holding one body, whose header marks the new
  account as current (`test_panel_ui.py`).
- The drawer, the query, the selected project and the selected session survive
  a swap; the colour target does not (`test_panel_ui.py`).
