# Setting up Waybar for CCAS

`install.sh` writes the modules themselves — a fenced managed block in
`~/.config/waybar/config.jsonc` and one `custom/cc-<slug>` module per account.
That part needs no work from you.

What it does **not** touch is `~/.config/waybar/style.css`. CCAS deliberately
owns no styling: the bar's look is yours, and a tool that rewrites your
stylesheet on every install is a tool that eventually loses a change you made.
So the appearance of the modules — spacing, hover, press feedback — is set up
once, by hand, from this page.

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

The click opens the same picker `ccs <slug>` opens in a terminal. CCAS used to
emit `menu`, `menu-file` and `menu-actions` here instead, pointing at a
generated `menu.xml` per account; it no longer does. Waybar parses `menu-file`
once when the module is built, so keeping that menu current meant reloading the
whole bar every time a Claude session appeared — see
`docs/superpowers/specs/2026-07-25-fuzzel-only-menu-design.md`.

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

## Spacing, hover and press

Add to `~/.config/waybar/style.css`, one selector pair per account slug:

```css
/* CCAS account modules — spacing, hover and press feedback. */
#custom-cc-vsed,
#custom-cc-vo-se-15th {
    padding: 0 9px;
    margin: 0 3px;
    border-radius: 4px;
    transition: background-color 120ms ease;
}

#custom-cc-vsed:hover,
#custom-cc-vo-se-15th:hover {
    background-color: rgba(255, 255, 255, 0.10);
}

#custom-cc-vsed:active,
#custom-cc-vo-se-15th:active {
    background-color: rgba(255, 255, 255, 0.22);
}
```

Notes:

- **The selectors are per-slug.** GTK CSS has no attribute or prefix matching,
  so `#custom-cc-*` is not a thing — a new account gets no styling until you add
  its id here. That is the cost of CCAS not owning the stylesheet.
- `:hover` works — measured on this setup, the background lifts to exactly the
  `rgba(255,255,255,0.10)` composite. **`:active` never fires**, also measured:
  the custom module is EventBox-backed and GTK's active state is a button
  concept, so a held button leaves the background at its hover value. The rule
  above is kept only so the intent is on the page; delete it if a dead selector
  bothers you, and do not spend time tuning its alpha.
- Colour per account belongs in the label, not here — it comes from the
  registry's palette index via `ccs color <slug> <n>`, so a CSS `color` rule
  would fight it.

## Applying changes

| change | what makes it visible |
|---|---|
| `style.css` | `killall -SIGUSR2 waybar` |
| `config.jsonc` (module added/removed) | `killall -SIGUSR2 waybar` |
| label markup (`label.py`) | `./install.sh`, then the next render tick |
| account settings (colour, nickname, display) | nothing — `ccs` signals `SIGRTMIN+n` itself |

A per-module `SIGRTMIN+n` repaints the **label only**, which since CCAS stopped
using `menu-file` is the only thing on the bar a setting can change. Waybar does
cache `menu-file` at startup; that is exactly why CCAS no longer uses one.

**`ccs` runs the installed copy, not the repo.** Editing `label.py` and then
running `ccs render` tests the code as of the last `./install.sh`. Re-install
before every live check.
