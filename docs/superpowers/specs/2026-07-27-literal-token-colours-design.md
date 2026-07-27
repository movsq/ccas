# Literal token colours, and the glyph stops being a token

2026-07-27

## The bug this comes from

The user spent ten minutes on the panel's colour editor before working out that
the control they were dragging did nothing, and that this was not a fault.

Their `vsed` account rendered every token from a literal hex. Nothing in its
label was set to `account`, so the account's own colour reached no part of the
bar. The panel's colour editor nonetheless offered "the widget" as its first
target, with a live hue slider, a live saturation slider, a live hex field and a
live preview swatch, all wired to a value with no path to anything on screen.
Every part of the control said it was working. Nothing said the label did not
use it.

That is the complaint `auto` earned, arriving through a different door. `auto`
was retired because a format string could not tell you what it would look like;
`account` has the same defect in the other direction — a colour setting cannot
tell you whether it will be seen.

`account` was also only ever half-retired. The panel's `account` chip was
removed on the user's request earlier the same day, but the value stayed legal
in the registry and stayed the default for every new account. The UI stopped
offering a thing the data model kept using.

## What goes

**`format.ACCOUNT` is deleted.** A token's colour is a hex or it is nothing, and
nothing is `format.DEFAULT_COLOR` — white, the same answer for every token.
`valid_color` accepts hexes only. `_resolve` disappears; `_chosen` is the whole
of it. `ccs format <slug> --color <token> account` stops being accepted; `-`
still clears.

**`%icon` is deleted as a token.** The glyph was never a piece of layout — it is
the widget's identity, which is why "the widget" and "the icon" kept collapsing
into each other in conversation. Two names for one colour is what made the
editor unreadable. So the glyph is drawn by the renderer ahead of the format
string, in the account colour, and `hide_icon` keeps its existing meaning as the
one thing that removes it.

`TOKENS` loses its `%icon: None` entry and with it the special case in `_emit`.
`_icon(ctx)` stays, but `render()` calls it directly rather than reaching it
through the token table, and it takes its colour from `account["color"]` rather
than from `_chosen(account, "%icon")` — there is no per-token entry left to
consult. `hide_icon` still wins over the colour, for the reason already
recorded there: it is the invisibility toggle, and a colour that resurrected the
glyph would make the panel's checkbox lie.

## What the account colour becomes

One colour with one meaning: **the account's colour**, painting the ✻ in the bar
label and the dot beside the account in the panel's own list. There is no
token-level override that could disagree with it, so there is nothing left to
reconcile, and the control is never inert — moving it always changes something
visible in both places.

## The picker

The `Gtk.DropDown` goes. It reads as disabled, it hides every colour but the
selected one, and it costs a click and a popup to change target.

In its place, a horizontal row of chips, one per target:

```
Color of

  ┌──────────┐  ┌────────────┐  ┌────────────────┐
  │   icon   │  │  %5hreset  │  │  %5hquotaleft  │   ← selected chip
  │  ▁▁▁▁▁▁  │  │  ▁▁▁▁▁▁▁▁  │  │  ▁▁▁▁▁▁▁▁▁▁▁▁  │     has a filled bg
  └──────────┘  └────────────┘  └────────────────┘

  hue  ───────────●──────────────
  sat  ─────●────────────────────
  #89b4fa   [preview]
```

Each chip is a `Gtk.Button` whose child is a vertical box: the target's name in
the panel's ordinary text colour, and under it a 2 px-high `Gtk.Box` inset by a
small horizontal margin, filled with that target's current colour.

The name stays white because a name painted in its own colour is unreadable at
low saturation and indistinguishable from "unset" at white — the strip carries
the colour instead, where legibility does not depend on it.

Selection is a filled background, never a colour cue: the strip means "this is
the colour", the background means "this is what you are editing", and the two
must not be confused for one another.

Each chip carries **its own `CssProvider` at `PRIORITY_USER + 1`**, for the
reason the preview swatch already does — a colour part-way through a drag
belongs to none of the classes `_load_tints()` precomputed, and `menu.css` is
installed at `PRIORITY_USER`, so an application-priority provider paints
nothing. The selected chip's strip is repainted on every slider motion, so it
tracks the drag; the others are painted once when the editor is built.

The first chip is the account colour, labelled `icon` — it paints the glyph in
the bar and the dot in the panel's account list, and `icon` is what the user
calls it. The rest are the tokens of the format string, in the order they
appear.

The write path is unchanged: the `SETTLE_MS` timer, `_commit`, and
`panel.NO_REBUILD` keeping the sliders alive across an applied setting. Clicking
a chip re-seeds the sliders and commits nothing, exactly as changing the
dropdown's selection did.

## Data model

`panel.color_targets()` keeps its shape — a list of `{token, label, color}` —
with two changes: the first entry's label becomes `icon` rather than
`the widget`, and a token holding an unrecognised value seeds from
`format.DEFAULT_COLOR` rather than from the account colour. Seeding from the
account colour was defensible while `account` existed; now it puts a colour in
the sliders that has nothing to do with the token, and a commit then writes it.

`DEFAULT_FORMAT` becomes `%email %5hused` — the glyph is no longer named in it,
because it is no longer a token.

`DEFAULT_FORMAT_COLORS` loses its `%icon` key for the same reason, leaving
`{"%email": GREY, "%5hused": <the account's hex>}`. It can no longer be a module
constant: `%5hused` now holds a literal that differs per account. So it becomes
a function of the chosen colour, and `registry.add()` draws one
`format.random_color()`, writes it to the account's `color` and to `%5hused`,
and leaves `%email` grey. The visible result of creating an account is
unchanged: glyph and used-percentage in one random colour, the address grey
between them — the glyph now because it follows `color` by definition rather
than by an `ACCOUNT` value.

## Nothing migrates

Both of the user's accounts hold values that stop being valid: `dim` on
`%5hreset`, `account` on `vo-sedlacek`'s `%5hquotaleft`. They render white,
which is what `registry.load()` already does with anything it does not
recognise, and the user re-picks them. No read path learns to understand a
retired value.

Their two stored format strings still contain `%icon`, which would print as the
literal text `%icon` on the bar. That is not left to a read-path rewrite either:
the implementer runs `ccs format <slug> '%5hreset %5hquotaleft'` for both
accounts once, as an explicit step, and says so.

## Tests

- `format`: `ACCOUNT` is gone and `valid_color("account")` is False; a stored
  `account` renders `DEFAULT_COLOR`; `%icon` is not in `TOKENS`, is reported by
  `unknown_tokens`, and renders as literal text; `render()` emits the glyph span
  ahead of the format string, in the account colour, and `hide_icon` still
  wins over it.
- `registry`: a new account's `format_colors` hold hexes only, the glyph and the
  used-percentage share one, and it equals the account's `color`.
- `cli`: `--color <token> account` is rejected; `-` still clears.
- `panel`: `color_targets` labels the first entry `icon`; an unrecognised token
  colour seeds `DEFAULT_COLOR`, not the account colour.
- `panel_ui`: the boundary tests that pin `gi` to `show()` must keep passing —
  the chip row is built inside the same function and imports nothing new at
  module scope.

## Non-goals

The chips are not a colour palette — they choose a target, not a colour. The
sliders and the hex field remain the only way to set one.

The account colour does not become settable from the bar, from `ccs format`, or
from anywhere `ccs color <slug> <hex>` does not already reach.
