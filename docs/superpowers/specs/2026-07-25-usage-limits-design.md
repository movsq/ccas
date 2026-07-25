# Per-account usage limits on the bar

2026-07-25.

Show, per account, how much of the 5-hour window is spent and when it clears —
on the Waybar label and in the account's menu. `docs/usage-limits-research.md`
is the measurement this rests on: four retrieval routes, three rejected, and the
reason the reset time and not the percentage is the thing worth displaying. Read
it first; nothing here re-argues it.

## What the feature is for

CCAS is an account *chooser*. The question it exists to answer at a glance is
"which of these can I use right now" — and that question is a quota question.
So usage is not decoration on the bar, it is the bar's subject, and it appears
in every display mode including `icon only`.

## Retrieval: `ccs statusline`, a recording wrapper

Route D of the research doc — the documented statusline hook. Two constraints
decide its shape, and both were found on the live system rather than assumed:

1. Every account's `settings.json` is a **symlink to `~/.claude/settings.json`**.
   CCAS cannot write it without breaking the invariant the whole design rests
   on. So CCAS ships the hook and the user wires it, the same stance CCAS takes
   on `style.css`.
2. **The user already has a statusline** — `~/.claude/statusline.sh`, printing
   `user@host dir`, context tokens, and `/rc`. Installing ours would destroy it.

So the wrapper *chains*. It never replaces:

```json
"statusLine": {
  "type": "command",
  "command": "/home/fixed/.local/bin/ccs statusline /home/fixed/.claude/statusline.sh"
}
```

`ccs statusline [delegate [args…]]`:

1. Read all of stdin, once, as text.
2. Record (below). Every failure is swallowed — a statusline that raises is
   visible in every session, and recording is the optional half of this command.
3. If a delegate was given, run it with the **same bytes** on its stdin and pass
   its stdout through verbatim; return its exit code. No delegate: print
   nothing, return 0. No timeout — a hung delegate hangs identically without the
   wrapper, and inventing one here would change behaviour the user did not ask
   to change.

Because `settings.json` is shared, wiring it once covers every account *and* the
default account. That is a feature, not a leak — see the guard below.

### Which account, and the invariant

The wrapper takes the account from `CLAUDE_CONFIG_DIR`, which Claude Code
exports into the statusline process. It records only when that resolves to a
directory **directly under `paths.accounts_root()`**, compared through
`os.path.realpath` on both sides.

Unset, or `~/.claude` — the default account, which includes the session an agent
is most likely running in — records **nothing at all**. The never-write-to-
`~/.claude` guarantee therefore holds for the new code path by construction, not
by care, and `test_relink_never_touches_mtimes_in_claude_home` gains a sibling
that runs the wrapper against a default-account environment and asserts no mtime
under `claude_home()` moved.

### `usage.json`, and why writes are rare

Recording writes `<account dir>/usage.json`, atomically, in the normalised shape
below. `paths.USAGE_FILE` names it and `paths.BLOCKLIST` gains it, so `relink`
never links a `~/.claude/usage.json` over a real per-account file — the same
treatment `.claude.json`, `menu.xml` and `history.tsv` already get.

**The write is conditional on a value actually changing.** The statusline fires
on the order of every few hundred milliseconds; utilisation moves every few
minutes. Writing unconditionally would mean thousands of writes an hour and, far
worse, would make the next paragraph unaffordable.

On a write — and only on a write — the wrapper calls
`waybar.signal(account["signal"])`. A per-module signal repaints the **label
only** and never re-reads `menu.xml`, which is exactly the right instrument
here: no flicker, no menu invalidation, and the cost is one `pkill` per real
change. `CCAS_NO_RELOAD=1` suppresses it as everywhere else. Absent the signal
the 30 s exec tick picks the change up anyway, so this is latency, not
correctness, and it is allowed to fail silently.

`fetched_at` is therefore "when this reading was first seen", not "when the hook
last ran". That is the more useful of the two and the only one obtainable
without a write per tick.

## `ccas/usage.py`

One module owning read, normalise, classify, format. Nothing else parses either
input shape.

### The normalised shape

```json
{ "fetched_at": 1784999999.0,
  "source": "statusline",
  "five_hour": { "percent": 94.0, "resets_at": 1785000599 },
  "seven_day": { "percent": 19.0, "resets_at": 1785259199 } }
```

`percent` is 0–100 float. `resets_at` is **unix epoch seconds**, always. Either
window may be `null`: the docs are explicit that each is independently absent,
and a payload with no `rate_limits` at all (before the first API response, or a
non-subscriber) must leave any existing file untouched rather than overwrite a
good reading with an empty one.

### Two inputs, one shape

| source | percent | resets_at | fetched |
|---|---|---|---|
| statusline `rate_limits` | `used_percentage` | epoch seconds | now |
| `.claude.json` `cachedUsageUtilization` | `utilization` | ISO 8601 string | `fetchedAtMs` / 1000 |

`read(slug)` returns whichever of the two is fresher by `fetched_at`, so an
account that has never run the hook still shows whatever a `/usage` left behind,
and one that has run it is never dragged backwards by an older cache. ISO 8601
parsing goes through `datetime.fromisoformat`, which handles both the trailing
`Z` and the fractional seconds the real values carry.

### Classification — one rule covers staleness

Given a reading and `now`, each window is in exactly one state:

- **absent** — no reading, or this window missing from it.
- **open** — `resets_at` is in the **past**. The window rolled over since the
  reading; the old ceiling is gone. There is no next `resets_at` until the
  account is used again, so the display is a word, not a time — and this is the
  *good* state.
- **bounded** — `resets_at` is in the **future**. The reading is still inside
  the window it describes, so its percentage is a valid **lower bound**;
  usage only climbs within a window. Rendered with `≥`.

There is deliberately **no age cutoff**. A 5-hour reading older than five hours
necessarily has a `resets_at` in the past, so "stale" and "rolled over" are the
same test, and there is no threshold to pick or to get wrong.

### Constants

```python
YELLOW, PEACH, RED = "#f9e2af", "#fab387", "#f38ba8"   # as in paths.PALETTE
DIM = "40000"                                          # a pango alpha, not a colour
RAMP = ((95, RED), (80, PEACH), (50, YELLOW))          # below 50 → DIM
SEVEN_DAY_TAKES_OVER = 90
```

The three colours are the mocha values `paths.PALETTE` already carries, spelled
again here rather than indexed out of it: the palette is the user's per-account
colour choice and its order is theirs to change, so reading `PALETTE[6]` for
"yellow" would break the ramp the first time an account is recoloured.

## Display

### The bar label

The 5-hour reset clock, always, whenever that window is bounded:

```
✻ vsed 19:30      bounded — the clock, coloured by RAMP
✻ vsed            open, or absent: today's bar, unchanged
✻ vsed 7d 94%     escalation
✻ 19:30           icon only carries it too
```

- **Clock, not percentage.** `%H:%M`, local. A 5-hour window cannot be more than
  five hours out, so there is no day ambiguity to resolve.
- **Colour carries the level**, which is what lets the text stay a clock. Below
  50 % the clock renders at reduced alpha so it recedes rather than reads as an
  alert.
- **Escalation.** When `seven_day` is bounded at `≥ SEVEN_DAY_TAKES_OVER` *and*
  above `five_hour`, the 7-day window is the binding constraint and the label
  shows `7d NN%` in RED instead. Its reset is days away, so the clock is not the
  actionable fact there — the number is. This is the only case where the label
  mentions the 7-day window at all.
- **Open and absent render identically** — as the bar you have today. A label is
  a warning device; no pressure and no data both mean nothing to warn about. The
  difference between them is stated in words in the menu and in `ccs doctor`,
  which is where there is room to say it.

`label.render(account, index, usage=None)` stays pure and gains one span.
`cmd_render` supplies the reading.

### The menu

One insensitive title row, third line under email and nickname, always present:

```
vsedlacek1337@gmail.com
vsed
5h ≥94% · clears 19:30
──────────────────────────
New session
…
```

States, in the same slot:

| state | row |
|---|---|
| 5h bounded | `5h ≥94% · clears 19:30` |
| 5h open | `5h window open` |
| 7d bounded and notable | `7d ≥82% · clears Tue 09:00` |
| nothing recorded | `usage — statusline hook not wired` |

The 7-day row appears as a fourth line only when that window is bounded at
`≥ SEVEN_DAY_TAKES_OVER`; below that it is noise in a menu that is mostly about
launching sessions. `ccs usage` always shows both.

Reset formatting in the menu: `%H:%M` when the reset falls on the current
calendar day, `%a %H:%M` otherwise. A 5-hour window can cross midnight and a
7-day one always lands on another day.

**A usage change never triggers a reload.** `cmd_render` writes `menu.xml` only
when it is also going to reload, gated on `menu.session_set_changed()`, and that
gate does not learn about usage. So the row is refreshed whenever the menu is
rewritten for some other reason and is otherwise as old as the last reload.

That is acceptable *only* because the row is anchored on an absolute clock. A
stale `clears 19:30` read at 20:00 is still a true statement and visibly a spent
one; a stale bare percentage would be a lie. This is the same property that
rescues the whole feature from the freshness problem, applied twice. Making
usage force a reload would put the flicker back that `session_set_changed()` was
written to remove — a bar reload every few minutes, for a number the label
already shows live.

`menu.build_xml(account, sessions, usage=None)` stays a pure function of its
arguments; `menu.write` does the one disk read and passes it in.

### `ccs usage [slug]`

The same information without a bar, and the only place that always shows both
windows and the reading's age and source:

```
vsed         5h ≥94% clears 19:30    7d ≥19% clears Tue 09:00   statusline, 12m ago
vo-se-15th   no data — statusline hook not wired
```

## `ccs doctor`

One new check, read-only like everything else there: parse
`claude_home()/settings.json` and look at `statusLine.command`.

| state | result |
|---|---|
| command mentions `ccs_bin()` and `statusline` | ✓ |
| `statusLine` set to something else | ✗, with the exact replacement, that command threaded in as the delegate |
| no `statusLine` | ✗, with the JSON to add |
| file missing or unparseable | ✗, saying which |

**It fails, rather than reporting.** The state this ships in is "never wired",
and a check that passes on never-wired cannot tell the user the one thing they
need to know. It goes green on a single edit and then earns its keep by catching
the regression if `settings.json` ever loses the line. The counterargument —
that CCAS does not grade `style.css` either, so it should not grade this — is
recorded here so it is not re-litigated; it lost because `style.css` is
cosmetics CCAS never reads, and this is a wiring step a CCAS feature depends on.

The check reads `paths.claude_home()`, so it is `CCAS_HOME`-overridable and
tests never see the real file.

## Testing

`tests/test_usage.py` owns the module; the label, menu, cli and doctor tests each
gain the part they own. The ones that are the specification rather than coverage:

- The wrapper under a **default-account** environment (no `CLAUDE_CONFIG_DIR`,
  and again with it pointing at `claude_home()`) writes nothing and moves no
  mtime under `claude_home()`.
- The wrapper passes the delegate the exact stdin bytes it received and returns
  the delegate's stdout unchanged.
- A delegate that exits non-zero, does not exist, or writes garbage still leaves
  the recording intact — and a recording failure still runs the delegate.
- A payload with no `rate_limits` does not overwrite an existing `usage.json`.
- An unchanged payload does not rewrite the file and does not signal.
- Both input shapes normalise to the same dict, including a `Z`-suffixed ISO
  timestamp with fractional seconds.
- `read()` prefers the fresher of hook and cache, in both directions.
- Past `resets_at` → open; future → bounded with `≥`.
- All four display modes carry the label token, `icon only` included.
- A changed `usage.json` alone leaves `menu.xml` unwritten and fires no reload.
- Doctor's check passes only on a command naming both `ccs_bin()` and
  `statusline`.

## To verify on the real system

Not by reasoning, and not by asking the user to click:

1. `·` (U+00B7) against the bar's FontAwesome-first stack, with `pango-view` per
   the recipe in CLAUDE.md. `●`/`○` are the only vetted glyphs; if `·` renders
   from a different font it becomes two spaces.
2. The label's ink alignment with the new run present — `pango-view --markup` on
   the exact three-run markup, comparing extents, since `ICON_RISE` was tuned
   against two runs and a third at `TEXT_SIZE` sits beside the enlarged glyph.
3. A live `ccs statusline` under a scratch `CLAUDE_CONFIG_DIR`, with the mtime
   canary from CLAUDE.md around it, proving nothing under `~/.claude` moved.
4. `./install.sh` before any live check — `ccs` runs the installed copy.

## Not in scope

- Refreshing the numbers ourselves. Route B spawns a session per refresh and
  route C is an undocumented OAuth endpoint the research doc rejected on policy.
  CCAS reads what Claude Code leaves behind and nothing more.
- Notifying on exhaustion. The bar is the notification.
- Switching accounts automatically when one runs out. A chooser that chooses is
  a different feature and would need to be right about far more than this one.
