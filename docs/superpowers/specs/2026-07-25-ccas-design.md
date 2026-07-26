# CCAS — Claude Code Account Switcher

**Superseded in part, 2026-07-26.** The Waybar `GtkMenu` this designed is
gone twice over: `2026-07-25-fuzzel-only-menu-design.md` replaced it with a
fuzzel picker, and `2026-07-26-gtk4-panel-design.md` replaced that with the
GTK panel. The isolation model, the registry and the never-write-to-`~/.claude`
invariant are unchanged and still in force.

> Kept as the record of what was decided and why. It is not a description of how CCAS works now — `README.md` and `CLAUDE.md` are.

**Date:** 2026-07-25
**Status:** Approved design, ready for implementation planning

## Purpose

Switch between multiple Claude Code accounts from Waybar and from the terminal,
without altering any settings, themes, plugins, or session history. Each account
is an authentication identity only; everything else is shared.

Each account owns a coloured icon in Waybar. Clicking it opens a native GTK menu
offering a new session, resume-last, a full searchable history submenu, and
per-account display controls.

## Non-goals

- Managing API keys or third-party providers (Bedrock/Vertex/Foundry).
- Syncing settings between machines.
- Any modification of `~/.claude/` contents. CCAS only ever reads from it.

---

## 1. Isolation model

Each account is a `CLAUDE_CONFIG_DIR` populated almost entirely with symlinks
back into the real `~/.claude`:

```
~/.cc-accounts/
  accounts.json              registry (see §2)
  <slug>/
    .credentials.json        REAL FILE — the only true per-account secret
    .claude.json             REAL FILE — holds oauthAccount (account identity)
    menu.xml                 generated GtkMenu descriptor
    history.tsv              generated snapshot backing the menu's history slots
    settings.json     -> ~/.claude/settings.json
    plugins/          -> ~/.claude/plugins/
    projects/         -> ~/.claude/projects/
    file-history/     -> ~/.claude/file-history/
    CLAUDE.md         -> ~/.claude/CLAUDE.md
    statusline.sh     -> ~/.claude/statusline.sh
    …every other top-level entry symlinked
```

Launching is `CLAUDE_CONFIG_DIR=~/.cc-accounts/<slug> claude`.

**Verified during design:** an empty `CLAUDE_CONFIG_DIR` reports
`{"loggedIn": false}`, and the symlink layout above reports `loggedIn: true`
with the correct account — so Claude Code honours the symlink farm.

### Why not swap credentials in place

Rewriting `~/.claude/.credentials.json` on each switch mutates live config and
permits only one account at a time. The isolated model never writes to
`~/.claude`, so a bug in CCAS cannot corrupt the real configuration, and two
accounts can run concurrently.

### The `.claude.json` exception

`CLAUDE_CONFIG_DIR` relocates `~/.claude.json` *inside* the account directory
(confirmed: an empty probe dir created its own). That file holds `oauthAccount`,
so it cannot be a symlink to a shared copy. It also holds per-project trust
flags, MCP servers, and onboarding state, which therefore diverge per account.

Mitigation: at `ccs add` time, seed the new account's `.claude.json` from the
real `~/.claude.json` with `oauthAccount` removed, so project trust and MCP
servers carry over. They drift afterwards; this is accepted.

Never share one `.claude.json` between live processes. The binary contains
`saveConfigWithLock: re-read config is missing auth that cache has; refusing to
write to avoid wiping ~/.claude.json. See GH #3117` — concurrent writes to a
shared config are precisely the hazard that guard exists for.

### Self-healing relink

Claude Code updates introduce new top-level entries in `~/.claude`. Those would
land inside the account dir instead of being shared. `ccs relink` walks
`~/.claude`, symlinks anything missing from the account dir (excluding the
blocklist below), and prunes dangling symlinks. It runs on every launch.

Blocklist — never symlinked, always per-account real files:
`.credentials.json`, `.claude.json`, `menu.xml`, `history.tsv`.

---

## 2. Registry

`~/.cc-accounts/accounts.json`:

```json
{
  "default": "personal",
  "accounts": [
    {
      "slug": "personal",
      "nickname": "personal",
      "email": "someone@example.com",
      "color": 1,
      "display": "nickname",
      "hide_icon": false,
      "warned_invisible": false,
      "signal": 1
    }
  ]
}
```

`default` names the account used when a command carries arguments and so cannot
show a menu — `claude -p "…"` in a terminal, for instance (§9). It defaults to
the first account added and is changed with `ccs default <slug>`.

- `slug` — `[a-z0-9-]+`, derived from nickname or the email local-part, unique.
- `nickname` — optional. Menus and labels fall back to `email` when unset.
- `color` — index into the palette (§5).
- `display` — one of `nickname` | `index` | `claude code` | `icon only`.
- `hide_icon` — renders the glyph invisible but still clickable (§5).
- `warned_invisible` — one-shot latch for the invisible-combination warning.
- `signal` — `SIGRTMIN+n` used to refresh this account's Waybar module.
  Assigned from array position, 1-based. Waybar allows 1..(SIGRTMAX-SIGRTMIN),
  around 30, which bounds the account count well above any realistic need.

`index` for display purposes is the 1-based position in the array.

Writes are atomic: write to a temp file in the same directory, then `rename`.

---

## 3. The `ccs` CLI

Python 3, installed to `~/.local/bin/ccs`.

Python rather than Bash for three reasons: `jq` is not installed and every piece
of state here is JSON; `pytest` is available while `bats` and `shellcheck` are
not, so Python logic is testable and shell logic would not be; and the history
scanner (§8) depends on byte-level regex over file heads. Bash is reduced to the
single `claude()` shell function in §9.

| Command | Behaviour |
|---|---|
| `ccs list` | Table of accounts: index, slug, nickname, email, colour, display mode. |
| `ccs add` | Create dir, relink, seed `.claude.json`, run login, record email, prompt for nickname. |
| `ccs rm <slug>` | Move account dir to `~/.claude_trash/` (never delete), drop from registry, re-render. |
| `ccs nick <slug> [name]` | Set or clear nickname. Empty clears, falling back to email. |
| `ccs color <slug> <n>` | Set palette index. |
| `ccs display <slug> <mode>` | Set display mode. |
| `ccs hide <slug> toggle\|on\|off` | Toggle icon visibility. |
| `ccs relink [<slug>]` | Self-healing symlink pass. All accounts if slug omitted. |
| `ccs render [<slug>]` | Print the Waybar label; regenerate `menu.xml` + `history.tsv`. |
| `ccs config` | Regenerate the managed blocks in Waybar config + reload. |
| `ccs launch <slug> <mode> [arg]` | Launch a session (§7). |
| `ccs default [<slug>]` | Show or set the default account. |
| `ccs tty [args…]` | Terminal entry point. No args opens the picker; with args, runs under the default account. |
| `ccs manage <action>` | GUI account management invoked from the menu: `add`, `rename <slug>`, `remove <slug>`. |
| `ccs <slug>` | Open that account's mode menu (TTY front-end). |
| `ccs <slug> <cmd…>` | Run `<cmd…>` with that account's `CLAUDE_CONFIG_DIR`. |

All generated Waybar commands reference `ccs` by absolute path
(`/home/fixed/.local/bin/ccs`), since Waybar's environment is inherited from the
compositor and cannot be assumed to include `~/.local/bin` on `PATH`.

The passthrough is what makes `ccs work claude -p "…"` work: any command,
including `claude auth status` or a headless prompt, runs under that identity
with no menu.

### `ccs add` flow

1. Prompt for a nickname (optional; may be set later).
2. Derive a provisional slug; create `~/.cc-accounts/<slug>/`.
3. `ccs relink <slug>`.
4. Seed `.claude.json` from `~/.claude.json` minus `oauthAccount`.
5. Spawn a terminal running `CLAUDE_CONFIG_DIR=… claude auth login`; the user
   completes the browser OAuth flow.
6. On exit, read `CLAUDE_CONFIG_DIR=… claude auth status` (JSON) for the email.
   Abort and roll the directory back to `.claude_trash` if `loggedIn` is false.
7. Assign the next free colour and signal; append to the registry.
8. `ccs config` to regenerate Waybar and reload.

### Recursion guard

`~/.local/bin/claude` is the real binary shim, and the install adds a `claude()`
shell function. Every internal invocation must therefore call the binary by
explicit path (`$CCAS_CLAUDE_BIN`, default `~/.local/bin/claude`), never the
bare name — a bare `claude` inside a spawned login shell would re-enter the
picker forever. `CCAS_INNER=1` is exported into launched sessions as a
belt-and-braces guard that makes the shell function fall through to the binary.

---

## 4. Waybar integration

One `custom/cc-<slug>` module per account, always visible, inside a managed
fenced block in `~/.config/waybar/config.jsonc`:

```jsonc
"custom/cc-personal": {
  "exec": "/home/fixed/.local/bin/ccs render personal",
  "interval": 30,
  "signal": 1,
  "tooltip": false,
  "menu": "on-click",
  "menu-file": "/home/fixed/.cc-accounts/personal/menu.xml",
  "menu-actions": {
    "new":           "/home/fixed/.local/bin/ccs launch personal new",
    "last":          "/home/fixed/.local/bin/ccs launch personal last",
    "search":        "/home/fixed/.local/bin/ccs launch personal search",
    "hist-0":        "/home/fixed/.local/bin/ccs launch personal hist 0",
    "…":             "…through hist-299",
    "disp-nickname": "/home/fixed/.local/bin/ccs display personal nickname",
    "disp-index":    "/home/fixed/.local/bin/ccs display personal index",
    "disp-cc":       "/home/fixed/.local/bin/ccs display personal 'claude code'",
    "disp-icon":     "/home/fixed/.local/bin/ccs display personal 'icon only'",
    "hide":          "/home/fixed/.local/bin/ccs hide personal toggle",
    "color-0":       "/home/fixed/.local/bin/ccs color personal 0",
    "…":             "…through color-7",
    "mng-add":       "/home/fixed/.local/bin/ccs manage add",
    "mng-rename":    "/home/fixed/.local/bin/ccs manage rename personal",
    "mng-remove":    "/home/fixed/.local/bin/ccs manage remove personal"
  }
}
```

The fenced block follows the convention already used in `~/.config/sway/`,
marking the region as machine-written:

```
// ══════════════════════════════════════════════════════════════════════
// ═══ CCAS — managed block, start ═══
// <model> — <date time TZ> — Claude Code account switcher modules
   …generated modules…
// ═══ CCAS — managed block, end ═════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════
```

`modules-right` also needs the module names inserted. That insertion is
likewise managed, replacing any prior CCAS entries rather than appending.

### Fixed history slots

`menu-actions` lives in `config.jsonc`, not in the XML. If slot count tracked
the live session count, every new session would rewrite the Waybar config and
force a reload.

Instead the block declares a **fixed 300 `hist-N` slots**. `menu.xml` supplies
only as many items as there are sessions (currently 219); unused slots are
simply absent from the XML. `config.jsonc` therefore changes only when accounts
are added, removed, or renamed.

`ccs launch <slug> hist N` resolves `N` against `history.tsv`, the snapshot
written atomically alongside `menu.xml`, so labels and targets can never
disagree. Sessions beyond 300 are reachable through the search row.

### Update paths

| Change | Mechanism | Cost |
|---|---|---|
| Colour, display mode, hide icon | Registry write, then `pkill -RTMIN+<n> waybar` | Instant, no flicker |
| New session appears in history | `exec` regenerates `menu.xml` every 30 s | None visible |
| Account added / removed / renamed | Regenerate managed blocks, `killall -SIGUSR2 waybar` | Brief bar flicker, rare |

Because colour and display mode are Pango markup in the `exec` output rather
than CSS, **no per-account CSS is generated at all** and `style.css` needs at
most a single padding rule for the hit area.

---

## 5. Label rendering

`ccs render <slug>` prints one line of Pango markup:

| Mode | Output |
|---|---|
| `nickname` | `<span color='#f38ba8'>✻</span> personal` |
| `index` | `<span color='#f38ba8'>✻</span> 1` |
| `claude code` | `<span color='#f38ba8'>✻</span> claude code` |
| `icon only` | `<span color='#f38ba8'>✻</span>` |

`✻` approximates the Claude mark and is present in the configured Nerd Font
fallback chain.

**Palette** — eight Catppuccin Mocha colours, matching the existing
`#89b4fa` / `#b4befe` in `style.css`:

```
0 peach  #fab387      4 teal   #94e2d5
1 red    #f38ba8      5 green  #a6e3a1
2 mauve  #cba6f7      6 yellow #f9e2af
3 blue   #89b4fa      7 pink   #f5c2e7
```

### Hide icon

`hide_icon` wraps the glyph in `<span alpha='1'>✻</span>` — Pango renders it at
1/65535 opacity, invisible while **retaining its full width**. The module stays
laid out and hit-testable, unlike `display: none`.

In `icon only` mode with `hide_icon` set, the module renders nothing visible but
remains clickable at its usual position — the intended, explicitly requested
behaviour.

### Invisible-combination warning

When `hide_icon && display == "icon only"` becomes true and `warned_invisible`
is false: `notify-send` *"it's still there — invisible."* and latch
`warned_invisible = true`. Whenever the combination is not true, clear the latch.
Re-entering the combination therefore warns again, exactly once each time.

Nicknames are Pango-escaped before interpolation.

---

## 6. The menu

`menu.xml` is a GtkBuilder descriptor with a root `GtkMenu` of id `menu`.

```
 personal                      ← title, insensitive; nickname or email
 ──────────────
 New session
 Resume last session
 Resume from history      ▸ ──┐
 ──────────────               │  > ...                    ← opens fuzzel
 Display as              ▸    │  ──────────────
 ☐ Hide icon                  │  0.0h  ~/      Build Waybar switcher
 Color                   ▸    │  0.2h  ~/4s    Implement Blender zone backend
 ──────────────               │  …all sessions, GTK scrolls
 Manage                  ▸
```

- **Display as ▸** — four items with state in the label: `● nickname`,
  `○ index`, `○ claude code`, `○ icon only`.
- **Hide icon** — `☐` / `☑` in the label.
- **Color ▸** — a plain list of the eight colour names. No tinting, no CSS
  classes.
- **Manage ▸** — `Add account…`, `Rename…`, `Remove this account…`. Each opens
  a fuzzel prompt or confirmation; removal always moves the directory to
  `~/.claude_trash/` rather than deleting it.

GtkMenu cannot filter, so `> ...` delegates to fuzzel with the full set. Every
row below it is directly clickable.

All labels are XML-escaped.

---

## 7. Launch flows

One engine, two front-ends.

**GUI (Waybar):** pickers are `fuzzel --dmenu`; the session opens in `kitty`.

**TTY (`claude` in a terminal):** pickers are `fzf`; the session `exec`s in
place, replacing the shell.

### TTY flow

Terminal mode is scoped to the current directory:

```
~/4s $ claude
┌─ account ────────────────────┐
│ personal │ work │ alt        │
└──────────────────────────────┘
┌─ mode ───────────────────────┐
│ New here            (~/4s)   │
│ Resume last in      ~/4s     │
│ History in          ~/4s…    │
│ ──────────────────────────── │
│ All projects…                │
└──────────────────────────────┘
```

`All projects…` widens to the global list. `ccs <slug>` skips straight to the
mode menu.

### Modes

| Mode | Action |
|---|---|
| `new` | Project picker, then `cd <dir> && claude` |
| `new <dir>` | `cd <dir> && claude` |
| `last` | Newest session (globally, or in cwd for TTY); `cd` to its recorded `cwd`; `claude --resume <uuid>` |
| `hist <N>` | Resolve slot `N` in `history.tsv`; `cd` to its `cwd`; `claude --resume <uuid>` |
| `search` | fuzzel/fzf over all sessions, then as `hist` |

Resume never asks for a directory — every session records its own `cwd`.

The project picker lists directories that have at least one session, sorted by
recency, filtering out any that no longer exist. Currently 12, all present.

Every launch runs `ccs relink <slug>` first.

---

## 8. History extraction

Session transcripts live at `~/.claude/projects/<slug>/<uuid>.jsonl` — 219
top-level files, 402 MB. Each records `cwd` in its early records, and most carry
an `ai-title` giving a human label.

**Algorithm:** for each file, read a **64 KB head** and regex for the first
`"cwd"` and the first `"aiTitle"`. Only when the head yields no title, re-read
the file in full (38 of 219 files). Sort by mtime, descending.

**No cache, no index.** Measured:

```
json.loads per line, early break     17.1 ms
regex on 64 KB head + fallback        5.1 ms      ← chosen
full parse of every line             970   ms
```

The win comes from not parsing JSON, not from reading less — the chosen method
reads *more* bytes (15.9 MB vs 7.1 MB) and is still 3.4× faster. At 5 ms a
rebuild is cheaper than maintaining anything to invalidate.

**First title, not last.** 176 files carry multiple `ai-title` records but only
4 ever change, and in those the first is the better label — later ones degrade
to slugs (`Build kprobe-based mouse button Mod4 remap module` →
`kprobe-mouse-mod4-remap`). Stopping at the first is both faster and better.

Capping the fallback read was measured and rejected: it saved nothing, because
the titleless files are small.

Files without a title fall back to their directory and timestamp as the label.

### Row format

```
0.2h  ~/4s    Implement Blender zone backend PR 1
```

Age is humanised (`m`/`h`/`d`), paths are `~`-abbreviated. `history.tsv` holds
`label \t uuid \t cwd` per line; the menu and the search picker both index into
it, so a click can never resolve to the wrong session.

---

## 9. Terminal integration

The install adds to `~/.bashrc`, inside a managed fenced block:

```bash
claude() {
  if [ -n "$CCAS_INNER" ]; then command claude "$@"; else ccs tty "$@"; fi
}
```

Bare `claude` opens the picker. **With arguments, no menu is shown** — the
command runs immediately under the account named by `default` in the registry,
so `claude -p "…"` behaves as it always did. To direct arguments at a specific
account, use the passthrough: `ccs work claude -p "…"`.

## 9a. First run

With an empty registry there are no account modules and therefore nothing to
click, so the managed block emits a single placeholder module instead:

```jsonc
"custom/cc-setup": {
  "format": "<span alpha='28000'>✻</span>",
  "tooltip": true,
  "tooltip-format": "No Claude accounts configured — click to add one",
  "on-click": "/home/fixed/.local/bin/ccs manage add"
}
```

It is replaced by real account modules as soon as the first account is added.
The same placeholder returns if every account is removed.

---

## 10. Install / uninstall

`install.sh` — idempotent, safe to re-run after a system update:

1. Install `ccs` to `~/.local/bin/ccs`.
2. Create `~/.cc-accounts/` and the registry if absent.
3. `ccs relink` for every account.
4. Regenerate the managed blocks in `config.jsonc` and `.bashrc`.
5. Reload Waybar.

`uninstall.sh` strips the managed blocks from `config.jsonc` and `.bashrc`,
removes `ccs`, and leaves `~/.cc-accounts/` intact unless `--purge` is given —
in which case it moves it to `~/.claude_trash/`, never deleting.

Managed-block editing is by start/end sentinel match, so hand-written config
outside the fences is untouched. Every file is backed up next to itself before
first modification.

---

## 11. Open risk

**Does Waybar re-read `menu-file` on each popup, or cache it at startup?**

Unknown; must be settled by a spike before building the menu generator.

- **Re-reads:** the 30 s `exec` regeneration keeps history fresh with no reload.
  This is the assumed design.
- **Caches:** history freshness needs `killall -SIGUSR2 waybar` after each
  regeneration. Since a rebuild costs 5 ms, the only real cost is the bar
  flicker. Mitigation would be to regenerate only when the session set actually
  changed — cheap to detect by comparing the newest mtime and file count.

Either way the design holds; only the refresh trigger differs.

A second spike should confirm that `menu-file` supports nested submenus and that
`menu-actions` can address items inside them. If nested submenus turn out
unsupported, the fallback is a flat menu whose `Resume from history` and
`Color` rows open fuzzel pickers instead of submenus.

---

## 12. Testing

- **Registry** — add/remove/rename round-trips; slug collisions; atomic writes.
- **Relink** — new entry in `~/.claude` gets linked; dangling links pruned;
  blocklisted files never linked.
- **History** — correct `cwd`/title extraction against fixtures: no title,
  multiple titles, title beyond 64 KB, malformed JSON lines, empty file.
- **Label rendering** — all four display modes × hide on/off; Pango escaping of
  a nickname containing `&` and `<`.
- **Warning latch** — fires once on entering the combination, re-arms on exit.
- **Managed blocks** — idempotent rewrite; surrounding config preserved;
  uninstall leaves the file as it was.
- **Empty registry** — the placeholder module is emitted with zero accounts and
  disappears once one exists; removing the last account restores it.
- **Default account** — `claude -p "…"` with no accounts fails cleanly; with
  several, it uses `default`; removing the default reassigns it.
- **Signal reassignment** — removing a middle account renumbers signals and
  indices, and the regenerated config matches the registry.
- **Isolation** — `claude auth status` under each account dir reports that
  account; `~/.claude/.credentials.json` is never written.

The isolation test is the important one: it is the guarantee the whole design
rests on.
