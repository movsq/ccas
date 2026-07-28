# On-demand usage poll when the panel opens

2026-07-28.

## The problem

`poll.py` and its systemd timer keep an idle account's usage reading no more
than five minutes old. That is enough for the bar, which is glanced at, and not
quite enough for the panel, which is opened *because* someone wants to know
where an account stands right now.

The obvious fix — retime the timer — is the wrong dial. Two measurements say so:

- `GET /api/oauth/usage` **is rate limited.** Claude Code's own bundle carries a
  `rateLimitedVia` path (`"envelope"` or an HTTP status) and degrades `/usage` to
  *"Showing last-known usage … (rate limited — try again in a moment)"*, falling
  back to persisted or header-seeded numbers. `poll.fetch()` has no such
  fallback: a 429 is `FAILED — reading unchanged`, which looks exactly like the
  staleness the poller exists to remove.
- The timer's benefit is bounded. The payload is a 5-hour and a 7-day window, so
  five minutes of staleness is 1.7% of the shorter one, and window **rollover**
  — the case that once emptied an idle account's label — needs no fetch at all,
  because `usage.state()` derives IDLE from `resets_at` locally.

A background poll pays for freshness continuously, whether or not anyone is
looking. A panel open is a human asking, and it is human-paced. That is the
cheaper place to spend a request.

## What is built

Opening the panel starts one background fetch for the account it was opened on,
rate limited to at most one per minute per account. When it returns, the two
usage bars in the open panel update in place. Nothing else changes, and nothing
is ever shown about a fetch that was skipped or failed.

## Why a new timestamp, and not `due()`

`poll.due()` is the existing gate, and reusing it with a shorter interval was the
first design. It does not work.

`due()` measures `fetched_at`, which `usage.record` defines as *"when this
reading was first seen"* — a deliberate consequence of the write-on-change rule,
which is what makes the statusline hook affordable at a few hundred
milliseconds. A reading that is not moving is not rewritten, so `fetched_at`
stays old, so `due()` is **permanently true** for exactly the account this
feature serves: the idle one, whose numbers are steady.

Gating the panel on `due()` would therefore fetch on every single panel open, at
any interval, and the rate limit would never engage.

So the guard carries its own timestamp — *when we last asked*, as against *when
the answer last changed*. The two mean different things and always will, which
is why the stamp is its own file rather than a key inside `usage.json`. Folding
them together either breaks write-on-change or reintroduces the hole above.

**The stamp is written before the fetch, not after.** A fetch that fails, or
hangs for the full five-second timeout, still counts as having asked — otherwise
a broken network becomes an unthrottled retry across repeated panel opens, which
is the precise behaviour the limit exists to prevent.

## Boundaries

The panel gains no decision. The split follows the one the repo already keeps.

| module | what it gains |
|---|---|
| `paths.py` | `POLL_STAMP_FILE`, in the account directory, and in `BLOCKLIST`. |
| `poll.py` | `PANEL_INTERVAL`, the stamp read/write, and `poll_on_demand()`. |
| `panel.py` | `refresh_usage(slug, now)` — the usage rows, rebuilt. No GTK. |
| `panel_ui.py` | one daemon thread, and an in-place update of the two rows. |

`poll_on_demand` returns the same `Outcome` namedtuple `poll_account` returns and
inherits its never-raises contract. Its fetcher is injected, so no test opens a
socket.

**Renewal is off on this path.** An expired token is `EXPIRED` and no fetch;
`poll_on_demand` passes a refusing `renewer`. Spawning `claude mcp list` on a
panel open is a process and up to 45 seconds for something the five-minute timer
already does — by the time a panel is opened, the timer has renewed. The
renewal stays the timer's alone.

The bar is still signalled when the reading changes: `record_reading` returning
True is a write, and `waybar.signal()` rides on a write, from the panel process
like everywhere else.

## The crossing back

**The thread starts in `_open`**, once, for the account the panel was opened on
— not in `build_body`, which would make a chip switch a second trigger. A switch
mid-flight still lands the reading on disk; it simply does not repaint.

**It is a daemon thread.** `poll.fetch` has a five-second timeout, and a
non-daemon thread keeps the process alive for up to that long after Escape — the
panel visibly gone, `ccs --gui` still running, the lock still claimed. Daemon
means the process exits and the fetch is abandoned, which is safe precisely
because the stamp was already written.

**A generation token, not widget liveness.** `ui_state` gains a `generation`
counter; `build_body` increments it and each body captures its own value, and
the `GLib.idle_add` callback drops the result if the two differ. This is the
hazard `_retire` documents — a callback reaching a body whose closure cells
CPython has cleared — but handled upstream of widget lifetime, so it is testable
as plain Python and cannot be defeated by GC timing.

**The rows update in place; the body is not rebuilt.** Reusing `_body_swapper`'s
`rebuild` would tear the body out from under a user who is mid-drag on a colour
slider or typing in the search entry, which is the grab-losing bug CLAUDE.md
already records for `Gtk.Scale`. So the usage lines are extracted from
`_build_header` into `_build_usage(rows)` returning `(box, apply_rows)`, and
`apply_rows` sets a `ProgressBar` fraction, swaps its tint class and sets a
label. `_load_tints()` already installs the whole of `usage.RAMP`, so a bar
crossing a threshold needs no new CSS.

## Failure is silent

Guarded, unchanged, HTTP error, rate limited, no usable token — none of them
reach the screen. The bars keep showing the recorded reading. This matches
`ccs statusline`, which swallows every recording failure because a statusline
that raises is visible in every prompt of every session, and it matches the
panel's existing refusal to talk about staleness at all. The evidence lives in
`ccs doctor`'s freshness row.

The thread body wraps the call even though `poll_on_demand` cannot raise:
an exception on a Python thread prints a traceback into Waybar's log, which is
the noise the silent-failure choice rules out.

## Tests

- `test_poll.py` — the guard skips inside `PANEL_INTERVAL`; the stamp is written
  *before* the fetch, proven with a fetcher that raises; an old stamp lets it
  through; the renewer is never called even for an expired token; the on-demand
  interval is a constant of its own, not `INTERVAL`.
- `test_paths.py` — the stamp is in `BLOCKLIST`.
- `test_panel.py` — `refresh_usage` returns the shape `build_state` puts in
  `state["usage"]`; `test_panel_never_imports_gi` still passes.
- `test_panel_ui.py` — real widgets, as that file already does: `apply_rows`
  moves the fraction and the label, and a stale generation applies nothing.

The generation-guard test is the kind that can pass for the wrong reason, so it
is mutated under the implementation per CLAUDE.md: break the comparison, confirm
red, restore.

## Not built

- Polling on a chip switch. One line to add later if it is wanted.
- Any indicator of staleness or of a failed refresh.
- Changing `INTERVAL` or the timer's cadence. Noted separately: `poll.INTERVAL`
  is a second copy of the unit's five minutes, so `systemctl --user edit
  ccas-poll.timer` cannot take the cadence below it, which contradicts what
  CLAUDE.md says about the cadence living in the unit alone. Out of scope here.
