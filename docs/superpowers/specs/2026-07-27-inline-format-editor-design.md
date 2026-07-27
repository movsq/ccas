# Inline format editing in the panel

The format string is the only widget setting that still leaves the panel. The
"Edit format…" button emits `panel.Action("edit_format", slug, None)` and
`cli.dispatch_panel` spawns kitty running `ccs format <slug> --edit`, which
prints the token table rendered against the account and prompts. Every other
setting in that drawer — skip permissions, headless, hide the icon, the account
colour, each token's colour — is applied in place through `panel.STAYS_OPEN`.

The three manage verbs open a terminal because they need free text, a
confirmation, or an interactive login and the panel is gone by the time they
run. The format string needs free text too, but nothing about it needs the
panel to close: it is a widget setting, and the panel is where the widget's
other settings live. The rendered preview is also better in the panel than in
the terminal, because the panel can show the real pango.

## What is built

A format editor inside the Settings drawer, above the existing colour chips —
the format string decides which tokens that row offers, so it reads top down.

```
Bar label
What this account's widget says, and how it is coloured

format   [ %email %5hused                              ]
         ✻ vsed@gmail.com  42% used
         unknown token: %bogus
insert   [nickname] [email] [index] [5h used] [5h left] [5h reset] …

Color of
[icon] [email] [5h used]
…sliders, hex entry…
```

- **The heading** is a title and hint pair, styled like the Settings row's own
  (`.ccas-settings-title` / `.ccas-settings-hint`, reused rather than
  duplicated) so that the block reads as widget customisation rather than as a
  bare text field between two checkboxes. It covers the format editor and the
  colour editor, which are one subject. It is appended by `_build_toggles`
  immediately before the format editor, inside the drawer box.
- **The entry** is seeded with the account's current format string.
- **The preview** is `format.render()` on the typed text with the account's
  real usage reading — the same call the bar makes, markup and all, so a colour
  already set on a token shows there. A `Gtk.Label` with `use_markup`,
  ellipsized at the end so a long format cannot widen the card. It carries the
  ✻ exactly as the bar does — drawn unless `hide_icon`, since the preview is
  the widget, not the format string.
- **The unknown-token line** is present only when `format.unknown_tokens()`
  answers a non-empty list. It warns; it never blocks the write. An unknown
  token renders as its own name on the bar, which is visible and
  self-explaining — the existing rule, unchanged.
- **The insert chips** are one per `format.TOKENS`, labelled with
  `format.NAMES[token]` — the human name, not the token spelling, the same
  choice the colour chips make. Clicking one inserts the token at the caret.
  `%%` is not a chip; it stays a thing you type.

The "Edit format…" button, the `edit_format` action kind, and the
`dispatch_panel` branch that spawned kitty for it are deleted. Nothing
generates a terminal for the format any more.

`ccs format <slug> --edit` stays. It is now reachable only by typing it, which
is the point: a TTY and an ssh session cannot run GTK, and the two doors differ
in their toolkit, not in their verbs.

## When it writes

On **Enter or focus-out**, and only when the text differs from what the entry
was seeded with.

Not a settle timer. The colour sliders use one because every position a drag
passes through is a colour; typing `%5hused` passes through `%5`, `%5h` and
`%5hus`, each a valid but different format, so a pause mid-word would write the
registry and repaint the bar with a half-typed label. Enter is the commit and
focus-out is the safety net, so a change is neither lost by clicking away nor
written before it is finished.

The seeded-text comparison is what keeps the write-only-on-a-change rule:
opening the drawer, clicking into the entry and clicking out again writes
nothing and signals nothing.

**The insert chips are `can_focus=False`.** A focusable button steals focus on
click, which is a focus-out, which would commit a half-built format between
every inserted token. This is the same class of bug as committing a colour
mid-drag.

The action is `panel.Action("format", slug, text)`; `cli.dispatch_panel` gains
one branch, `_mutate(slug, "format", value)` — the entry point `ccs format`
already uses. The panel gains no write path of its own.

## Rebuild

`format` joins `panel.STAYS_OPEN` and is **not** in `panel.NO_REBUILD`:
changing the format changes the token set, so `panel.color_targets()` answers a
different list and the colour chip row has to be rebuilt to match. Without the
rebuild, adding `%7dused` to the format leaves no way to colour it until the
panel is reopened.

The rebuild destroys the entry the commit came from. `ui_state` — which already
survives `refresh_toggles()` for the selected colour target — carries a flag
set at commit time; the rebuilt editor reads it, clears it, grabs focus and
puts the caret at the end. So Enter leaves the caret where it was typing.

`ui_state["target"]` is already clamped to the new list's length, so a format
change that removes the token being coloured selects the last remaining target
rather than raising.

## Where the logic lives

`panel_ui` parses nothing, the way it parses nothing now.

`panel.py` gains `format_previewer(slug, now=None)`: it reads the registry and
the usage reading once and returns a closure taking the typed text and
answering `(markup, unknown_tokens)`. Called once per editor build, so a
keystroke is a render and not a registry read. It is a plain function over
plain data with no GTK in it, which is what keeps it inside the test suite.

`panel.build_state()` is unchanged — the previewer is a callable and callables
do not belong in the state dict.

## Testing

`test_panel.py`
- `format_previewer` renders the typed text, not the stored format.
- It answers the unknown tokens of the typed text.
- It renders against the account's real usage reading, so a token with a colour
  set appears with that colour in the markup.
- `format` is in `STAYS_OPEN` and is not in `NO_REBUILD`.
- `edit_format` is gone: `dispatch_panel` raises `ValueError` for it.

`test_cli.py`
- `dispatch_panel(Action("format", slug, "%name"))` writes the format through
  `_mutate` and returns 0.
- It goes through `_mutate`, so `_refresh` runs and the bar is signalled — the
  same path `ccs format <slug> '<fmt>'` takes, with no second write path.
- No branch of `dispatch_panel` spawns a terminal for the format any more.

`test_panel_ui.py` — the commit rules, in the style the settle-timer tests
already use:
- Seeded text, focus-out, no change: nothing is applied.
- Edited text, Enter: applied once, with the typed text.
- Edited text, focus-out: applied once.
- An insert chip does not commit.
- An insert chip inserts at the caret, not at the end.

## Not in scope

- Validating or rejecting a format string. Unknown tokens warn.
- A `%%` chip.
- Any change to how the label renders, to `format.py`'s token table, or to the
  colour editor's behaviour.
