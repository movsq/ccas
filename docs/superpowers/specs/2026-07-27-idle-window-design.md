# An idle window is information, not ignorance

Measured 2026-07-27. One account's label read `✻ 14:50 100%` at 14:14 — its
5-hour window exhausted, clearing at 14:50. At 14:50:32 the poller wrote a
payload with `"five_hour": null`, and the whole label collapsed to a bare glyph.
The panel's 5h row said **no data**. Nothing had been misconfigured and the
format string was untouched: the window had simply rolled over, and with the
account idle the endpoint stopped reporting a 5-hour window at all.

`usage.state()` collapses two different facts into `ABSENT`:

- there is no reading — we know nothing;
- there is a fresh reading, and it says this window is not running.

The second is not ignorance. It means the quota refilled. Rendering it as
nothing costs the label of every account that is not in use — which is most of
them, most of the time, and includes the account you most want to look at right
after it hits 100%.

## The three kinds, redrawn

| kind | meaning | percent |
|---|---|---|
| `BOUNDED` | the window is running, its reset is ahead | as recorded |
| `IDLE` | there is a reading, and this window is not running: the payload named no window, **or** its reset has passed | `0.0` |
| `ABSENT` | there is no reading at all | `None` |

`OPEN` is deleted, folded into `IDLE`. One rule for "the window is not running",
however we learned it — a payload that omits the window and a reset that has
passed are the same fact, and the percentage `OPEN` used to carry is dead the
moment its window rolls over. Nothing migrates; the kind is computed per read,
never stored.

`IDLE` carrying `0.0` rather than `None` does most of the work with no new
branches: `format._percent` already returns `""` only for a `None` percent, so
`%5hused` renders `0%` and `%5hquotaleft` renders `100%` unchanged. `_reset` and
`_timeleft_token` already gate on `BOUNDED`, so they stay empty — an idle window
has no reset to name. It also resolves the ambiguity `panel.usage_rows` warns
about: a bar at zero now *means* zero, because ignorance is a separate kind.

## How it is said

Not "full" — it reads backwards, as full of usage rather than full of quota.

| surface | `IDLE` | `ABSENT` |
|---|---|---|
| `ccs usage` column | `5h 0% used` | `5h —` (unchanged) |
| picker line (`usage.lines`) | `5h 0% used` | omitted (unchanged) |
| panel row (`panel_ui._usage_text`) | `0% used` | `no data` (unchanged) |

`usage.bar()` still answers `None` for anything but `BOUNDED`: it exists to warn,
and an idle window has nothing to warn about. So `%5h` and `%7d` stay blank.

"no data" survives only where it is true — no reading at all — and `ccs doctor`'s
hook and timer checks remain the actual answer to it.

## The result

An account whose format is `%5hreset %5hquotaleft` reads `✻ 100%` while idle and
`✻ 19:30 43%` the moment it is used again, instead of losing its label entirely.
