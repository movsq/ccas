# Custom display format — design

2026-07-26

## The problem

Two faults, one cause.

`label.render()` appends the usage token to **every** display mode, including
`icon only`. So "icon only" is not icon only — it is an icon and a clock — and
there is no way to ask for the icon alone, nor to ask for the percentage instead
of the clock, nor to drop the clock while keeping a nickname. The four modes name
a *text* choice, but the label they produce is a text choice plus a hardcoded
usage decision the user never made.

The cause is that the label is a fixed shape with a fixed set of slots. The fix
is a fifth mode whose shape is the user's: a format string.

Separately, the panel's display dropdown carries no label. It sits beside the
colour swatches saying `nickname` and nothing says what that is a nickname *for*.

## What is being built

1. A fifth display mode, `custom`, driven by a per-account format string.
2. Per-token colour, chosen in the panel, stored beside the format.
3. The usage token removed from all four built-in modes.
4. The panel's bottom row labelled.

## The tokens

Four per window, named so the name is the documentation:

| token | 5-hour | 7-day |
|---|---|---|
| reset clock | `%5hreset` → `13:40` | `%7dreset` → `Tue 09:00` |
| time until reset | `%5htimeleft` → `2h20m` | `%7dtimeleft` → `2d21h` |
| quota used | `%5hused` → `62%` | `%7dused` → `100%` |
| quota remaining | `%5hquotaleft` → `38%` | `%7dquotaleft` → `0%` |
| smart auto | `%5h` | `%7d` |

Identity tokens: `%icon` (the ✻ at `label.ICON_SIZE`/`ICON_RISE`, honouring
`hide_icon`), `%name` (the nickname, **empty when cleared** — the same rule
`label.render` already keeps, and for the same reason: a cleared nickname means
"show no text", not "show my email address"), `%email`, `%index`.

`%5h` and `%7d` are `usage.bar()` unchanged: the clock when the window is
bounded, nothing when it is open or absent, and the 7-day window taking the label
above `usage.SEVEN_DAY_TAKES_OVER`. This matters — it means the default format

```
%icon %name %5h
```

reproduces today's bar exactly, so the migration to per-mode honesty does not
cost anyone their clock.

`%7dreset` uses `usage.reset_time()` rather than `reset_clock()`: a 7-day reset
can be days out and a bare `09:00` would be a lie about which day.

### Rules

- `%%` is a literal `%`.
- The longest matching token wins, so `%5hused` is never read as `%5h` followed
  by `used`.
- An **unknown token renders as its own literal text**: `%bogus` puts the four
  characters `%bogus` on the bar. Nothing ever disappears, the mistake names
  itself on screen, and the module can never render blank — which is the failure
  mode that matters, because a blank Waybar module is indistinguishable from a
  crashed one.
- A token whose window is **absent** renders empty, and the runs of whitespace
  around it collapse, so an unwired hook leaves no stray gaps.

## Storage

`accounts.json` gains two per-account keys:

```json
"old": {
  "display": "custom",
  "format": "%icon %name %5hreset",
  "format_colors": {"%name": "auto", "%5hreset": "#f9e2af"}
}
```

`format` defaults to `%icon %name %5h`. `format_colors` defaults to `{}`, and a
token absent from it is `auto`.

Three colour values:

- `auto` — the token's **own** colour: the `usage.RAMP` for usage tokens (dim,
  then yellow at 50, peach at 80, red at 95), the account's palette colour for
  `%icon`, plain for text tokens. The default, so an untouched custom format
  looks like the bar does now.
- `dim` — `usage.DIM` alpha.
- `#rrggbb` — a literal colour.

`registry.set_field` validates: `format` must be a string, `format_colors` a dict
whose values are `auto`, `dim`, or a `#rrggbb` hex. It does **not** reject
unknown tokens — see below.

`paths.DISPLAY_MODES` becomes
`["nickname", "index", "claude code", "icon only", "custom"]`.

## `ccas/format.py`

A new module, because this does not belong in `label.py`: `label.py` owns the
bar's fixed shape and its measured pango metrics, and this owns a parser.

Its whole surface:

```python
TOKENS          # token -> (text_fn, auto_color_fn), the single table
render(account, index, usage, now) -> str   # pango markup
unknown_tokens(fmt) -> list[str]
```

No I/O, no GTK, no registry — a pure function of the account dict and the usage
reading, which makes it the most testable thing in the repo. `test_format.py`
sits beside it.

`format.py` imports `ICON_SIZE`, `ICON_RISE`, `TEXT_SIZE` from `label.py` rather
than restating them. Those three were measured with `pango-view` against the
bar's real font stack and the pair must stay re-checked together; a second copy
would drift the first time one is tuned.

`label.render()` keeps the four built-in modes and delegates `custom` to
`format.render()`. Its `usage_bar()` call moves inside the `%5h`/`%7d` token
implementations, which is what removes the clock from the built-ins.

Text is `pango_escape`d. Colours are emitted by `format.py` itself and can only
come from the three validated shapes above, so no user text ever reaches the
markup unescaped — the format string is layout, never markup.

## The terminal door

```
ccs format <slug>                        # show the format, its colours, and a preview
ccs format <slug> '%icon %name %5h'      # set it
ccs format <slug> --color %name dim      # set one token's colour
ccs format --tokens                      # the table
```

Setting a format containing an unknown token **succeeds** and prints
`warning: unknown token %bogus`. Rejecting it would mean that removing a token in
a later version turns a stored format into a hard error with nothing on the bar;
rendering it literally turns the same event into visible, self-explaining text.

`ccs doctor` reports an unknown token in any account's format, names the account,
and says which command fixes it. It does not repair — doctor never writes.

Setting the format does not change `display`; it is stored whether or not the
mode is `custom`, so you can prepare a format and switch to it.

## The panel door

The bottom row of `_build_toggles` becomes labelled, and grows a second row that
exists only when `display == "custom"`:

```
Display as  [ custom      ▾ ]                                  Format…

Colour of   [ %5hreset    ▾ ]  [auto][dim][●][●][●][●][●][●][●][●]  #______
```

- **`Display as`** — the label the dropdown never had.
- **`Format…`** spawns kitty running `ccs format <slug>` through
  `cli._in_terminal()`, exactly as Rename and Remove already do. The panel has no
  free text and should not grow any: it is built fresh per click and its model is
  one click, one `panel.Action`, then close.
- **The token dropdown** lists the tokens actually present in *this* account's
  format, in the order they appear — not the whole table. Colouring a token the
  format does not use is not a thing anyone wants to do.
- **The swatch row** is the existing one plus two leading chips, `auto` and
  `dim`, and a trailing hex entry. The hex entry is the one text field, and it
  is a `GtkEntry` that emits on `activate` only — a colour is a small enough
  commitment that Enter-then-close is the right shape, where a rename is not.

Every control emits one `panel.Action` and the panel closes, unchanged.
`panel.build_state` grows `format` and `format_colors`; `panel_ui` decides
nothing, as always.

## Migration

An account with no `format` key gets the default on read; nothing is rewritten
until the user sets one. Existing accounts keep their `display` value, so the
only visible change on upgrade is that the clock leaves the four built-in modes.
That is the point of the change, and `%icon %name %5h` under `custom` brings it
back for anyone who wants it.

## Testing

- `test_format.py` — the token table, one test per token against a synthetic
  reading; `%%`; longest-match; unknown tokens rendering literally; absent
  windows collapsing whitespace; escaping; each of the three colour shapes; the
  `auto` ramp at each threshold; **the default format rendering byte-identical
  to today's `label.render` for a nickname account** — the migration canary.
- `test_label.py` — the four built-in modes no longer carry a usage token, and
  `icon only` with a live reading is exactly the icon.
- `test_registry.py` — validation of both new fields, and their defaults.
- `test_panel.py` — `build_state` carries both fields; the token dropdown's
  contents follow the format.
- `test_cli.py` — the four `ccs format` forms and the unknown-token warning.
- `test_doctor.py` — an unknown token is reported and nothing is written.

## Out of scope

Raw pango markup inside a format string, per-token font sizes, and conditional
tokens ("show this only when above 80%"). The colour ramp already carries
pressure, and the smart `%5h` token already carries presence.
