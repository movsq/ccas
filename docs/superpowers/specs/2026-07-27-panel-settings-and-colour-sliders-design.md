# One format, one colour editor, one Settings drawer

Status: **designed 2026-07-27**, not yet built. Supersedes the display-mode half
of `2026-07-26-custom-display-format-design.md`; the format string and its
tokens survive unchanged, the four modes around it do not.

## Why

The panel's Widget Customisation block grew a control at a time and was never
regrouped. What it looks like now, bottom to top:

```
WIDGET CUSTOMISATION
☐ hide the icon
Show [custom ▾] [Edit format…]   Colour  ●●●●●●●●
Colour of [%icon ▾] auto dim ●●●●●●●● [#rrggbb]
```

Two rows, one above the other, both labelled with the word Colour, both showing
the same eight swatches, meaning entirely different things: the first is the
account's colour, which tints the whole widget, and the second is one token's
colour inside the format string. The second row is five controls wide — a
dropdown, two chips, eight swatches and a hex field — for a setting that only
exists when the display mode happens to be `custom`.

The mode dropdown is the root of it. Once a format string can say `%icon` and
nothing else, "icon only" is a preset pretending to be a mode, and every branch
that asks *which* mode this account is in exists to serve four presets that the
format string already expresses.

## Decision

Three changes, in the order they should be built.

### 1. `custom` is the only display mode

`label.render()` becomes a delegate to `format.render()`. Deleted along with the
branch: `paths.DISPLAY_MODES`, the `display` field's validation in
`registry.set_field`, `ccs display <slug> <mode>`, `cli._display_menu()`, its
entry in the settings menu, `panel.shows_color_row()`, and the "Show" dropdown
in `_build_toggles`.

`format.DEFAULT_FORMAT` becomes:

```
%icon %5hreset %5hquotaleft
```

with default `format_colors` of `{"%5hreset": "dim", "%5hquotaleft": "account"}`
— the glyph in the widget's colour, the reset time dim beside it, the remaining
percentage in the widget's colour again. `%icon` stays `auto`, which for the
glyph already resolves to the account's colour.

**Migration is lazy, not an upgrade step.** `registry` normalises on read: an
account whose `display` is anything other than `custom` is given
`DEFAULT_FORMAT` and the defaults above, and the `display` key is dropped the
next time that account is written. No pass over `accounts.json` at install time
— rewriting a whole registry to fix a field is the blind overwrite that "read
the live state before you write it" exists to prevent, and on this machine both
accounts are already on `custom`, so the path only ever runs against a registry
we cannot see.

What survives: `hide_icon`, which is an override on `%icon` rather than a mode;
`Edit format…` and the terminal editor behind it, untouched; every form of
`ccs format`.

The cost, stated plainly: `nickname`, `index`, `claude code` and `icon only`
stop being one-click choices. "Icon only" is now `%icon` typed into the format
string — more expressive, less discoverable.

### 2. One colour editor, two sliders

Both colour rows collapse into a single control:

```
Colour of [ the widget ▾ ]   auto  dim  account   ⬤ #89b4fa
   hue        ├──────────●──────────────┤
   saturation ├─────────────────●───────┤
```

The dropdown lists **the widget** first, then each token present in this
account's format string. One pair of sliders edits whichever is selected, so
there is only ever one palette on screen — which is the whole point.

The chips are contextual. A token gets `auto`, `dim` and a new `account`; the
widget gets none, because a widget colour is always a literal.

**`account` is a new colour value** beside `auto` and `dim`, accepted by
`format.valid_color` and resolved in `_emit` and `_icon` to the account's
colour. It is what makes the default's percentage follow the widget: move the
hue slider with "the widget" selected and the glyph and the percentage move
together. Without it the default would have to bake a literal hex into every new
account and drift the moment the widget's colour changed.

**The colour model is hue and saturation at fixed lightness.** Measured, not
chosen — the eight palette colours span 73.3%–86.1% lightness (mean 78.2%) while
their saturation runs 54.1%–92.0%:

```
peach   H= 23.0  S=92.0%  L=75.5%     teal    H=170.0  S=57.4%  L=73.3%
red     H=343.3  S=81.2%  L=74.9%     green   H=115.5  S=54.1%  L=76.1%
mauve   H=267.4  S=83.5%  L=81.0%     yellow  H= 41.4  S=86.0%  L=83.1%
blue    H=217.2  S=91.9%  L=75.9%     pink    H=316.5  S=71.8%  L=86.1%
```

The palette is already a fixed-lightness hue ring; the sliders are its own
coordinates. **L is pinned at 0.78.** Saturation 0 gives a light grey that is
still legible on the bar's `#353535`, and no slider position can reach black or
white — a constraint worth having on a bar label.

On load, H and S are decomposed from the stored hex and the stored lightness is
shown as it is, so no colour shifts merely because the editor opened. The first
drag snaps that colour's L to 0.78; the existing pink at 86% darkens slightly
the first time it is touched, and not before.

The hue track carries a CSS gradient spectrum; the saturation track runs grey to
the current hue at full chroma and is regenerated when hue moves. The `#rrggbb`
entry stays as the escape hatch for a colour the sliders cannot reach —
`format_colors` accepts any hex and `ccs format --color` writes them.

**Storage: the account's `color` becomes a hex string**, not an index into
`paths.PALETTE`, so both dropdown targets write the same shape.
`registry.set_field` validates it against `format.HEX` — hex only, not
`format.valid_color`, which also admits `auto`, `dim` and `account`; those three
are token values and mean nothing for a widget's own colour. An integer is still
accepted and normalised through `paths.PALETTE[i][1]`, which covers
`ccs color <slug> 3` and any registry written by the old code. `paths.PALETTE`
itself stays — it is the migration table, the source of the 0.78 constant, and
what `registry.new_account` picks an unused colour from.

**Commit on release, never during the drag.** `usage.py`'s rule is write only on
a change and let `waybar.signal()` ride on the write; a live drag would fire both
on every motion event. `value-changed` drives the preview swatch only. The
swatch sits on `#353535`, the bar's own background, so an untuned preview is
still an honest one. `GtkScale` has no "released" signal in GTK4, so the commit
comes from a `GestureClick` on the scale plus an `EventControllerKey` for
arrow-key adjustment.

No new control flow is needed: `panel.STAYS_OPEN` already lists `color` and
`format_color`, and `pick()` routes those through `apply()` and a rebuild rather
than closing the window.

**Why not `Gtk.ColorDialog`.** GTK4's only non-deprecated colour APIs are
`ColorDialog` and `ColorDialogButton`, both of which open a toplevel window;
`ColorChooserWidget`, the one embeddable chooser, was deprecated in 4.10 with no
in-place replacement. A dialog over a layer surface has nothing meaningful to be
transient for, so it would float outside the panel's own scrim and Escape
handling. Owning two `GtkScale`s is smaller than owning that, and it encodes the
palette's actual geometry, which a general-purpose RGB dialog would not.

### 3. A Settings drawer

The unlabelled gear in the header becomes a full-width disclosure row at the
bottom of the card:

```
  ⌄  Settings
     Account and widget customisation
```

It toggles the `Gtk.Revealer` that is already there, with its existing
SLIDE_DOWN transition. The ✕ keeps the header corner to itself.

Inside: the manage verbs the revealer already holds (rename, remove, add), plus
`hide the icon`, `Edit format…`, the colour editor, and `headless runner`.

`skip permissions` **stays outside**, above the drawer. The two toggles are split
by whether they bear on the click being made: skip permissions changes how the
session this panel is about to launch will run, so its state should be visible
before pressing New session; `headless` only affects `ccs -p` from a terminal and
is never relevant to a panel click.

Collapsed, the panel is chips, usage, verbs, search, panes, one toggle and one
Settings row.

## Two things that will break if not designed for

**The rebuild destroys UI state.** Applying any setting fires
`GLib.idle_add(refresh_toggles)`, which tears down and rebuilds the whole toggles
box. Both the expanded flag and the selected colour target must live outside that
subtree — the same treatment the search text and the selected session already
get, and for the same reason.

**The card grows downward.** Expanded height must be checked on both outputs
rather than assumed; `DP-1` and `HDMI-A-1` are not the same size, and the layer
surface does not resize to fit its child.

## Testing

TDD inline, per the working style. Tests go in the file that owns the behaviour.

- `test_label.py` — the non-custom cases go; what remains is that `render()`
  delegates.
- `test_registry.py` — a hex `color` round-trips; an integer `color` is
  normalised to the palette hex; an account carrying an old `display` is
  normalised to the default format on read and loses the key on write.
- `test_format.py` — `account` is a valid colour; it resolves to the account's
  colour in both `_emit` and `_icon`; `DEFAULT_FORMAT` and its default
  `format_colors` render the intended label.
- `test_panel.py` — the colour target list is the widget plus the tokens in this
  account's format string; `shows_color_row` is gone.
- `test_cli.py` — `ccs display` is gone; `ccs color <slug> <hex>` and
  `ccs color <slug> <int>` both land as hex.

The HSL conversion is pure arithmetic and belongs with `format.py`'s other pure
functions, so it is unit-testable without GTK. `panel_ui.py` stays the module
that decides nothing.

Live verification after `./install.sh`, driven rather than clicked by hand:
`CCAS_PANEL_OUTPUT=<connector> setsid ccs --gui <slug>`, then `grim`, then the
`swaymsg seat - cursor move` recipe to drag a slider.

## Out of scope

A better format-string editor. `Edit format…` opening a terminal is fine as it
is, and the colour work does not depend on changing it.
