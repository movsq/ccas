# Setting up Waybar for CCAS

`install.sh` writes the modules themselves — a fenced managed block in
`~/.config/waybar/config.jsonc` and one `custom/cc-<slug>` module per account.
That part needs no work from you.

It also writes one fenced block at the **end** of `~/.config/waybar/style.css`,
holding the modules' own spacing and hover — and nothing else in that file. The
rest of your stylesheet is yours; the first time CCAS writes it, a copy of what
was there is kept beside it as `style.css.ccas-orig`.

That block used to be a hand-written section on this page, and the hand lost:
the selectors are per-slug, so an account removed and added back came back under
a new slug and its widget quietly stopped answering the pointer.

## What install.sh gives you

Per account, in the managed block:

```jsonc
"custom/cc-vsed": {
    "format": "{}",
    "return-type": "json",
    "exec": "…/ccs render vsed",
    "on-click": "…/ccs --gui vsed",
    …
}
```

plus the module ids appended to `modules-center`. The widget id you style is
`#custom-cc-<slug>` — the module name with the slash turned into a dash.

The click opens the CCAS panel — a GTK4 layer surface, one window, built fresh
each time. Clicking the same widget again closes it, as do Escape and a click
anywhere outside it. `ccs <slug>` typed in a terminal opens the fzf list
instead, because a TTY and an ssh session cannot run GTK.

CCAS used to emit `menu`, `menu-file` and `menu-actions` here, pointing at a
generated `menu.xml` per account; it no longer does. Waybar parses `menu-file`
once when the module is built, so keeping that menu current meant reloading the
whole bar every time a Claude session appeared — see
`docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md` and
`docs/superpowers/specs/2026-07-26-gtk4-panel-design.md`.

## Styling the panel

The panel's stylesheet is yours outright — CCAS has no managed block in it at
all, unlike `style.css`. `install.sh` copies `assets/menu.css` to `~/.config/ccas/menu.css` once and
never overwrites it; edit it and reopen the panel to see the change — nothing
needs reinstalling. The handles are `.ccas-scrim` (the full-output surface
behind the panel, dim it or make it `transparent`), `.ccas-panel`, and one class
per part: `.ccas-header`, `.ccas-chip`, `.ccas-usage-bar`, `.ccas-verb`,
`#ccas-search`, `.ccas-panes`, `.ccas-project-row`, `.ccas-session-row`,
`.ccas-toggles`, `.ccas-swatch`, `.ccas-manage`, `.ccas-close`, and
`.ccas-drawer` (the Settings drawer, which is drawn *over* the panes — keep it
opaque or the session list shows through it).

Unlike the bar, the panel is its own process and does not inherit Waybar's
FontAwesome-first font stack, so the glyph rule below does not apply to it.

`CCAS_PANEL_OUTPUT` pins the panel to a connector (`HDMI-A-1`). Unset, it opens
on the output the pointer is on.

## The font stack matters

The glyph is `✻` (U+273B) and the bar's font stack decides who draws it. A
typical stack here:

```css
* {
    font-family: FontAwesome, "JetBrainsMono Nerd Font Mono", monospace;
    font-size: 11px;
}
```

FontAwesome wins for any codepoint it happens to cover, which is why CCAS
vets glyphs against the *whole* stack rather than against the font it meant to
use. Before adopting any new glyph:

```bash
pango-view --font="FontAwesome, JetBrainsMono Nerd Font Mono, monospace 28" \
           -q -t '○ off  ● on  ✻' -o /tmp/g.png
```

## Sizing: the label is pango markup, not CSS

`ccs render` emits pango markup, so the glyph and the text beside it are sized
in `ccas/label.py`, **not** in `style.css`. A CSS `font-size` on the module
scales the whole label, glyph and nickname together — which is not what you
want if the point was to enlarge only the icon.

Three constants control it:

| constant | does |
|---|---|
| `ICON_SIZE` | glyph size, relative to the bar's font (`"150%"`) |
| `ICON_RISE` | glyph baseline shift, in 1024ths of a point (`"-800"`) |
| `TEXT_SIZE` | nickname/index size, relative to the bar's font (`"110%"`) |

`ICON_RISE` is load-bearing and non-obvious. Pango puts both runs on a shared
baseline, and `✻` carries more of its ink above that baseline than a digit does,
so an enlarged glyph rides visibly high next to its own label. A negative rise
drops it back. `TEXT_SIZE` exists for the mirror-image problem: at `ICON_SIZE`
the glyph is a pixel taller than 11px text, and the text then reads as floating.

If you change `ICON_SIZE`, re-check the pair. Measure it rather than eyeballing
it — render the exact markup and compare the ink extents of the two runs:

```bash
pango-view -q --markup --background=white \
  --font="FontAwesome, JetBrainsMono Nerd Font Mono, monospace 8.25" \
  -t "<span size='150%' rise='-800'>✻</span> <span size='110%'>1</span>" -o /tmp/p.png
```

Both runs should span the same top and bottom rows. This works with the bar
covered or off-screen, which the screenshot route does not.

The current values were also confirmed against the bar itself, once the usage
clock gave the label a third run to sit beside. `grim -o HDMI-A-1`, thresholded
above the `#313244` bar background, put the glyph and the clock on ink rows 9–28
alike — so `150%`/`-800` holds at the bar's real DPI, not only at pango-view's.

## The format string

There is one way a label is built: a format string of tokens, which you write
yourself. Everything outside a token is copied through literally, so spaces,
separators and stray text are all yours. The four named display modes this
replaced were presets the string already expressed — "icon only" is the empty
string, since the glyph is drawn whether the format asks for it or not.

```bash
ccs format vsed '%name %5hused %5htimeleft'
ccs format vsed                       # show it, and any per-token colours
ccs format --tokens                   # every token there is
```

The glyph `✻` is **not** a token. It is the widget's own mark, drawn ahead of
whatever the format string says, in the account's colour, and the only thing
that removes it is `ccs hide <slug> on`. `%icon` was a token once; it renders as
its own four characters now, like any other name the table below does not hold.

| token | renders |
|---|---|
| `%name` | the nickname (`display_name`'s first choice) |
| `%email` | the account email |
| `%index` | the account's position in the registry |
| `%5h` | the 5-hour window the way the old bar said it — the smart form |
| `%5hused` | percent of the 5-hour window used, `≥` when the figure is a lower bound |
| `%5hquotaleft` | percent of it remaining |
| `%5htimeleft` | how long until it resets, e.g. `1h32m` |
| `%5hreset` | the wall-clock reset time |
| `%7d…` | the same five, for the 7-day window |

An unknown token is **not** rejected — it renders as its own literal text on the
bar, which is visible and explains itself where a silent refusal would not.
`ccs format` warns when you set one and `ccs doctor` keeps naming it.

### Colour

A token's colour is a `#rrggbb` or nothing at all, and nothing means white —
the same white for every token. Set one at a time:

```bash
ccs format vsed --color %5h '#89b4fa'  # a hex, and only a hex
ccs format vsed --color %5h -          # clear it: back to white
```

`-` deletes the entry rather than storing a value, so `format_colors` only ever
holds deviations. The panel's Settings drawer has a **Color of** row: a chip per
target — the icon first, then every token in this account's format — each with a
2 px strip of its current colour, and hue and saturation sliders under them.
Clicking a chip re-seeds the sliders and writes nothing; the write happens when
a drag settles.

### Getting the old bar back

Before the format string existed, every display mode carried a hardcoded usage
clock. `%name %5h` is exactly what the old `nickname` mode printed — the glyph
in front of it comes for free:

```bash
ccs format vsed '%name %5h'
```

The default for a new account is `%email %5hused` — the address in grey and the
percentage of the 5-hour window used, with the glyph ahead of them. `%5hused` is
written with the account's own randomly chosen colour, so glyph and percentage
match from the first paint; they are two separate hexes from then on, and moving
both is two drags.

There is no `auto`, no `dim` and no `account`. `auto` made the answer depend on
which token you asked about, the saturation slider says what `dim` said, and
`account` made a colour setting that might reach the label and might reach
nothing — see `why.md` for the ten minutes that cost.

## Spacing and hover

Generated. `ccs config` — which `install.sh` runs, and so do `ccs add` and
`ccs rm` — rewrites this at the end of `style.css`:

```css
/* ═══ CCAS — managed block, start ═══ */
/* Generated by ccs. Do not hand-edit inside this region. */
#custom-cc-vsed,
#custom-cc-vo-sedlacek {
    padding: 0 9px;
    margin: 0 3px;
    border-radius: 4px;
    transition: background-color 120ms ease;
}
#custom-cc-vsed:hover,
#custom-cc-vo-sedlacek:hover {
    background-color: rgba(255, 255, 255, 0.10);
}
/* ═══ CCAS — managed block, end ═══ */
```

Notes:

- **The selectors are per-slug.** GTK CSS has no attribute or prefix matching,
  so `#custom-cc-*` is not a thing — which is exactly why this is generated. Any
  hand-written rules you have for these ids sit above the block and lose to it;
  delete them once they are stale. `ccs doctor` fails if a widget has no hover
  rule.
- **It goes last** so it outranks equal-specificity rules above it. Everything
  above is untouched, and `style.css.ccas-orig` is what was there first.
- **`:active` is not in it**, measured: the custom module is EventBox-backed and
  GTK's active state is a button concept, so a held button leaves the background
  at its hover value. A rule there would be inert. `:hover` does work — the
  background lifts to exactly the `rgba(255,255,255,0.10)` composite.
- Colour per account belongs in the label, not here — it is a hex string in the
  registry, set by `ccs color <slug> <#rrggbb>` or by the panel's hue and
  saturation sliders, so a CSS `color` rule would fight it.

## Applying changes

| change | what makes it visible |
|---|---|
| `style.css` | `killall -SIGUSR2 waybar` |
| `~/.config/ccas/menu.css` (the panel) | nothing — reopen the panel |
| `config.jsonc` (module added/removed) | `killall -SIGUSR2 waybar` |
| label markup (`label.py`) | `./install.sh`, then the next render tick |
| account settings (colour, nickname, display) | nothing — `ccs` signals `SIGRTMIN+n` itself |

A per-module `SIGRTMIN+n` repaints the **label only**, which since CCAS stopped
using `menu-file` is the only thing on the bar a setting can change. Waybar does
cache `menu-file` at startup; that is exactly why CCAS no longer uses one.

**`ccs` runs the installed copy, not the repo.** Editing `label.py` and then
running `ccs render` tests the code as of the last `./install.sh`. Re-install
before every live check.
