# GTK4 panel — replacing the fuzzel menu with an application window

Status: design, approved 2026-07-26. Supersedes the GUI half of
`2026-07-25-fuzzel-only-menu-design.md`.

## Why

The current GUI picker is a chain of fuzzel windows: pick an account action,
which opens another window, which opens another. That shape is inherited, not
chosen — it came from the Waybar `GtkMenu` the fuzzel picker replaced, and a
`GtkMenu` can only be a cascade of lists. fuzzel then kept the cascade while
losing the cascade's one virtue, the parent staying on screen.

The result is a maze for what is, in substance, a single small application:
one account, its quota, its sessions, and half a dozen toggles. Everything is
two or three clicks deep and nothing is visible until you go looking for it —
you cannot tell whether `--dangerously-skip-permissions` is on without opening
a submenu to read the mark next to it.

fuzzel also caps what the interface can be. It has no markup mode, no live
input hook, and its rows are fixed once stdin closes, so a search field that
filters a list underneath itself — the thing most wanted here — is not
expressible in it at all.

## Decision

Replace the GUI picker with a single GTK4 window, positioned by
`gtk4-layer-shell`, driven from Python via PyGObject.

Rejected alternatives, with the reason each lost:

- **rofi (Wayland fork).** A real improvement over fuzzel — per-row pango
  markup, a theme language, icons — but still a dmenu. It cannot hold two
  panes with a live filter over both, and it would leave the cascade intact.
- **eww.** The same GTK toolkit with a DSL over it. Instant to open because a
  daemon is already resident, but the interaction logic would live in `.yuck`
  and shell round-trips, outside the reach of the test suite that every other
  rule in this repo is pinned by. The daemon also inherits the environment of
  whatever started it, which is the trap `docs/why.md` records for Waybar.
  Rejected: the panel is opened a few times a day, so a warm daemon buys
  little, and it buys it with untestable logic.
- **Dear ImGui and the immediate-mode family.** No layer-shell backend. They
  render into an `xdg_toplevel`, so the window tiles under sway, cannot anchor
  to the bar, and does not dismiss on click-outside — menu semantics would have
  to be reimplemented. Also 50–150 ms of GL context and font atlas setup, a
  redraw every frame for a static list, and native wheels in the dependency
  path.

Layer-shell support, not memory, is what eliminates most of the field.

## Shape

One window. No nesting, no submenus, no navigation.

```
┌──────────────────────────────────────────────────────────────┐
│  ● Old  ┆  ○ New                                          ⚙  │
│  vsedlacek1337@gmail.com                                     │
│  5h  ▓▓░░░░░░░░  ≥2%   clears 13:40                          │
│  wk  ▓▓▓▓▓▓░░░░   61%  clears Thu                            │
├──────────────────────────────────────────────────────────────┤
│  [ New session in ~/4s ]        [ Resume last ]              │
├──────────────────────────────────────────────────────────────┤
│  🔍 ______________________________________________           │
│ ┌ projects ────────┬─ sessions in ~/4s ──────────────────┐   │
│ │ ~/4s        2m   │  2m    Check unmerged content from… │   │
│ │ ~/ccas      3m   │  2.6h  Implement Blender zone back… │   │
│ │ ~           42m  │  2.6h  Diagnose fullscreen stutter… │   │
│ │ ~/blender   3d   │  1d    Review Opus's level design…  │   │
│ └──────────────────┴─────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│  ☑ headless runner   ☑ skip permissions   ☐ hide icon        │
│  Display: [icon only ▾]        Colour: ● ● ● ● ● ● ●         │
└──────────────────────────────────────────────────────────────┘
```

### Header

The account whose bar module was clicked, plus a chip per other account.
Clicking a chip switches the panel to that account without closing it — the
full-sidebar version of this is deferred; at two accounts a chip row is the
right weight, and it becomes a sidebar unchanged if the count grows.

Below it, both quota windows as real bars, from `usage.read()` — the existing
three states and the `≥` lower-bound convention are unchanged, only their
rendering is. The reset time stays the headline number, not the percentage,
for the reason `docs/usage-limits-research.md` gives: `resets_at` is an
absolute anchor and needs no staleness cutoff.

### Verbs

`New session` and `Resume last`. These take their cwd from the **selected
project** in the left pane.

This deliberately changes a rule. CLAUDE.md pins that `cmd_mode_menu`'s launch
verbs differ by door — the GUI gets directory-free labels with `cwd=None`,
because a Waybar click inherits Waybar's working directory, and `resolve()`
ignoring cwd under `gui` is what made merged labels lie rather than merely
mislead. The panel removes that premise: it supplies a directory the user
picked explicitly. So the GUI verbs gain a real cwd and an honest label.

The terminal path is untouched and keeps its own verbs. The two lists are not
being merged; the GUI list is gaining a cwd it never had.

### Body

Two panes over the session history.

- **Left:** projects, from `history.project_dirs()`, sorted by the most recent
  session mtime within each. The first — the project changed most recently — is
  selected when the panel opens.
- **Right:** that project's sessions, `history.scan()` order (mtime descending).
  The top row is highlighted on open, so the session you were last in is one
  keypress away with nothing clicked.

`204 more…` stops existing. A `GtkScrolledWindow` scrolls, which is the
constraint the Task 0 spike measured against `GtkMenu` (219 items filled a
1440 px screen) and which no longer applies.

### Search

One field, filtering **both** panes: matching sessions on the right, and the
left column narrowed to projects that contain a match. Case-insensitive
substring over session title and project path. Selection follows the best
match.

Scoping the search to the selected project was rejected: a search is worth its
place precisely when you cannot remember which project something was in.

### Toggles

`headless`, `dangerous` and `hide_icon` as checkboxes; display mode as a
dropdown; colour as a swatch row. All visible, all showing their state without
being opened. `headless` stays exclusive across accounts via
`registry.set_headless()`.

The `label.MARK_ON` / `MARK_OFF` pair is not needed here. It exists because
Waybar's `style.css` leads with FontAwesome, which covers `☑` but not `☐`, so a
checkbox pair rendered from two fonts at two sizes and the checked state looked
empty. The panel is a separate process with its own stylesheet and does not
inherit that font stack. `MARK_ON`/`MARK_OFF` remain in use for the **bar
label**, which is still Waybar's.

### Manage

The `⚙` reveals an inline strip for rename, remove and add — a `GtkRevealer`
within the panel, not another window. Rare and semi-destructive, so it is the
one thing that is not visible by default; it is still not a separate surface.

## Architecture

The current GUI flow is a sequence of blocking picker calls, each returning a
string that decides the next call. The panel is one window returning one
action, so that structure collapses.

Two new modules, split so that only the widget tree is untestable — and so
that the split is enforceable by a test rather than merely intended:

| module | role |
|---|---|
| `ccas/panel.py` | pure. `build_state(slug)` (registry + usage + history → a plain dict), `filter_state(state, query)` (the search), and the `Action` type. **Never imports `gi`**, which `test_panel.py` asserts. |
| `ccas/panel_ui.py` | the widget tree. `show(state) -> Action \| None`. The only module that imports `gi`, lazily. |

`cli.py`'s `--gui` branch builds the state, calls `show()`, and dispatches the
returned action through the existing `launch.resolve()` / `registry` /
`accounts` entry points. No decision is made inside the widget tree; it emits
what was chosen and nothing else.

Styling lives in `~/.config/ccas/menu.css`, shipped once by `install.sh` and
never overwritten afterwards — the stance the repo already takes toward
`style.css` being the user's. Widgets carry CSS classes and ids from the start
(`.ccas-header`, `.ccas-usage-bar`, `.ccas-verb`, `.ccas-project-row`,
`.ccas-session-row`, `#ccas-search`) so the stylesheet has handles to grab.
Look and feel is then adjustable without a code change or a reinstall.

### Positioning and dismissal

`gtk4-layer-shell`, anchored to the top edge of the Waybar output
(`HDMI-A-1`), horizontally centred. Anchoring under the specific module is
deferred: Waybar hands the click no coordinates, so per-module placement needs
a measurement pass that centring does not.

Keyboard mode `ON_DEMAND` — the panel takes keyboard focus without locking the
compositor. Esc closes. Click-outside closes.

No open animation. Decided explicitly: process start is ~150 ms, and a fade on
top of that reads as sluggish rather than polished. Motion may be used later
for the manage revealer, where no process start is being paid for.

## Dependencies

PyGObject, GTK4 and `gtk4-layer-shell` become required for the GUI path.

The stdlib-only rule survives in the form that matters: `gi` is imported
**inside** `panel.py`, never at `cli.py` import time, so `ccs -p`, `ccs list`,
`ccs doctor`, `ccs statusline` and every non-GUI command continue to run on
stdlib alone. A missing PyGObject must never break the statusline hook or the
passthrough.

If the import fails on a `--gui` invocation, `ccs` reports the missing
dependency via `notify-send` and stderr rather than dying silently — a bar
click that does nothing with no message is the worst available failure. `ccs
doctor` checks for the three dependencies and names the package to install.

## What is not changing

- **The terminal path.** fzf stays exactly as it is. A TTY and an ssh session
  cannot run GTK, and `pickers.py` keeps its fzf branches. Its fuzzel branches
  go: once the panel owns every GUI path, `choose`, `prompt`, `prompt_or_clear`
  and `confirm_create` lose their `gui=True` halves, and `CREATE_ROW`,
  `CREATE_COLOR` and `HINT` — all three of which exist only to work around
  fuzzel — go with them. `is_gui()` stays; it still decides which door was
  used.
- **Every hard invariant.** Never write to `~/.claude`; never invoke `claude`
  by bare name; `claude` is the user's; never delete; `ccs doctor` never
  writes; a slug is permanent. The panel is a front-end and touches none of
  them.
- **The bar.** One module per account, each with its own label, colour and
  usage. `on-click` still runs `ccs --gui <slug>` through `waybar._ccs()`, so
  Waybar's config is unchanged and the `--gui` flag keeps its meaning. The two
  tests pinning that pair (`test_generated_commands_all_force_gui_mode`,
  `test_passthrough_does_not_force_gui_mode`) stay as they are.
- **`usage.py`, `history.py`, `launch.py`, `registry.py`.** The panel consumes
  their existing APIs. Row *formatting* moves into the panel, since a widget
  wants fields rather than a padded string; `history.format_row()` stays for
  the fzf path.

## Testing

`test_panel.py`, importing no GTK:

- `build_state()` against a scratch `CCAS_*` environment: correct account,
  both usage windows, projects ordered by most recent session, sessions
  ordered by mtime, the right defaults selected.
- `filter_state()`: matches title, matches path, case-insensitive, narrows the
  project column to projects containing a match, selection follows the best
  match, empty query restores everything.
- The action tuples `show()` can return dispatch correctly through `cli.py`.

The widget tree itself is not unit-tested. It is verified on the real system
per the repo's working style — `./install.sh` first, since `ccs` runs the
installed copy — with `grim` and the `swaymsg seat - cursor move` recipe for
clicking, never by asking the user to click things.

## Out of scope

Per-module horizontal positioning, open animations, compositor blur (needs
swayfx), per-row icons, a full account sidebar, and any redesign of the `ccs
add` login flow, which keeps its current behaviour under `⚙`.
