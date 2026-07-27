# Polling usage — making an idle account's numbers move

Status: **shipped 2026-07-27**. Extends
`2026-07-25-usage-limits-design.md`; the display it produces is unchanged, only
the freshness of what feeds it.

## Why

The bar's quota numbers come from one source: the statusline hook. Claude Code
runs it several times a second inside an interactive session and hands it both
windows, `ccs statusline` records them, and a change signals the module. For the
account you are working in, this is excellent — measured on 2026-07-27, `vsed`'s
`usage.json` was six seconds old while two of its sessions ran.

For an idle account it is nothing at all. The hook fires only inside a session,
so the moment you close the last one the numbers freeze. At the same instant the
`vsed` reading was six seconds old, `vo-se-15th`'s was five hours old, and the
widget for it showed a percentage from another era. The `.claude.json` cache does
not rescue this: only `/usage` inside a session writes it, which is another
session.

Two further symptoms come from the same root and are worth naming, because they
sound like separate bugs:

- **The bar appears to update at random.** It does not; `usage.record()` writes
  only when the numbers change, and utilisation moves every few minutes rather
  than on a clock. The 30-second render tick repaints an unchanged label.
- **Clicking seems to refresh it.** Opening the panel reads the same recorded
  file the bar reads. The click changes nothing; it is simply the moment the
  user looks.

So the requirement is not a faster repaint — the repaint is already every 30
seconds — but a source of numbers that exists when no session does.

## Decision

Poll `GET https://api.anthropic.com/api/oauth/usage` per account, on a systemd
user timer, every five minutes.

This is the route `docs/usage-limits-research.md` records as **C**, and that
document rejected it. The rejection was on policy, never on mechanics: the call
was confirmed live, HTTP 200 with both windows, no beta header, no special
user-agent, no rate limiting. The policy text it quotes prohibits third-party
developers offering Claude.ai login or routing requests through subscription
credentials **on behalf of their users**. CCAS does neither. It is one person's
tool, on one person's machine, reading that person's own two accounts' numbers
with tokens Claude Code itself put there. The user was shown the quoted
prohibition and the reasoning on both sides and chose this route; that decision
is recorded here so nobody re-litigates it from the research doc alone.

The endpoint costs no quota. It returns metadata, not inference.

### Rejected alternatives

- **Stay hook-only, display the age.** Honest, and nothing new can break — but
  the idle account's numbers still never move, which is the entire request.
  Worth noting the display already carries the fact that matters: `resets_at` is
  an absolute anchor, so a stale reading whose reset has passed is reported as
  "open" with no fetch at all. That is why polling improves the display without
  changing it.
- **Drive `/usage` in a headless session** (route B). Documented surfaces only,
  but it spawns a real session per refresh and breaks the moment that UI
  changes. Already rejected once, for the same reasons.
- **Poll inside `ccs render`.** Tempting — the tick exists, and there is no new
  machinery. But Waybar draws every module on every bar, so `render` runs once
  per monitor and would fetch twice per tick without a lock, and the cadence
  becomes a side effect of the bar's tick rather than a thing the user sets.
- **A resident `ccs pollerd`.** Most direct control, most new machinery: a
  process to start, supervise and restart, all of which the timer gets from
  systemd for free.

## Architecture

### `ccas/poll.py` — the new module

Owns the network and the credential **read**, and nothing else.

| function | what it does |
|---|---|
| `access_token(slug)` | reads the account's `.credentials.json`, returns `(token, expires_at)` or `None`. Read-only, always. |
| `fetch(token)` | `urllib.request` GET, 5 s timeout, `Authorization: Bearer …`. Returns the parsed payload or `None`. stdlib only — no new dependency. |
| `due(slug, now, interval)` | False when the recorded reading is already fresher than the interval. |
| `poll(slugs, fetcher=fetch)` | orchestration; returns one outcome per account. |

`fetcher` is a parameter so the tests never touch the network.

### `usage.py` — a third input shape

The module already owns "two sources, two shapes, one normalised reading".
`from_oauth(payload, now)` joins `from_statusline()` and `from_cache()` there,
because nothing outside `usage.py` may parse a source shape. The endpoint's
payload is the cache's shape — `utilization` as 0–100 and an ISO 8601
`resets_at` — reached without the `cachedUsageUtilization` wrapper.

`record()` splits: the existing entry point keeps taking a hook payload, and a
new `record_reading(slug, reading)` underneath takes an already-normalised one.
Both keep the write-only-on-change rule, and the poll signals the module on a
write exactly as `cmd_statusline` does today.

The reading is written to the same `usage.json` with `"source": "oauth"`, so
`usage.read()`'s freshest-wins precedence needs no change and `ccs usage` names
the source it already prints.

### Never refresh

`poll.py` reads `.credentials.json` and never writes it. A 401 or an expired
token records nothing and leaves the previous reading standing. Refreshing
rotates the token, and a botched write breaks that account's login — worse, a
rotation can invalidate the token a live session is holding.

The consequence is a horizon: an access token lives about eight hours and only
Claude Code renews it, so polling keeps an account fresh only while its token is
valid. Measured 2026-07-27: `vsed` 5.9 h remaining, `vo-se-15th` 2.6 h. Past
that the account goes quiet again until it is next used.

This is an acceptable degradation, not a hole. An account untouched for eight
hours has rolled its five-hour window over, and `usage.state()` reports that
from the timestamp alone.

### Cadence

Five minutes. Two accounts is 24 requests an hour — a fraction of what an active
session makes against the same endpoint — and it is finer than the data
warrants: 1.7 % of a five-hour window, 0.05 % of a seven-day one.

`due()` skips any account whose recorded reading is fresher than the interval,
so an account with a live session is never polled. The hook stays in charge of
the account being used; the timer serves the one sitting idle, which is the case
that is broken.

### The timer

`install.sh` writes `~/.config/systemd/user/ccas-poll.service` and
`ccas-poll.timer` and enables the timer:

```ini
# ccas-poll.timer
OnBootSec=1min
OnUnitActiveSec=5min

# ccas-poll.service
ExecStart=%h/.local/bin/ccs poll
```

The interval lives in the unit and nowhere else — one place, standard tooling,
no CCAS setting to drift out of sync with systemd. Control is then ordinary:
`systemctl --user stop ccas-poll.timer` kills it, `edit` retimes it,
`journalctl --user -u ccas-poll` says why a poll failed.

CCAS owns these two files and may rewrite them on install, but an existing file
goes to `~/.claude_trash` first — the never-delete rule, and the same courtesy
`style.css` and `menu.css` get.

### CLI and audit

`ccs poll [<slug>] [--force]` runs it by hand and prints one line per account:

```
vsed         ok 5h 41% 7d 12%  (signalled)
vo-se-15th   skipped, recorded 12s ago
```

and, for the three failure shapes, one of:

```
vo-se-15th   token expired 2h ago — no fetch
vo-se-15th   http 401 — reading unchanged
vo-se-15th   timeout after 5s — reading unchanged
```

`--force` ignores `due()`. Exit code 0 unless every account failed.

`ccs doctor` gains a check that the timer is enabled and active, and reports
each account's reading age and token expiry. A poller that quietly died looks
exactly like the staleness this feature exists to remove, so it must be visible
where drift is already policed. A timer the user deliberately stopped is
reported as off, not as a failure.

## What does not change

- **No new bar state for failures.** A failed poll shows the previous reading,
  which the absolute-anchor semantics already make honest. An error glyph would
  make the bar twitch at a transient network blip.
- **No new format tokens.** `%5hused`, `%5hquotaleft`, `%5htimeleft` and their
  `%7d` counterparts already exist. Polling makes their values true when no
  session is running; it adds no display surface.
- **No per-account opt-out.** A setting with no use case behind it yet.
- **`~/.claude` is untouched**, as ever. The poll reads an account directory and
  writes one file inside it.

## Testing

One new test file, `tests/test_poll.py`, plus additions to `test_usage.py` for
`from_oauth` and the `record_reading` split and to `test_doctor.py` for the new
check.

No test may open a socket: `poll()` takes its fetcher as a parameter, and the
tests pass a stub returning a canned payload, a 401, and a timeout. The
credential read is pointed at a scratch account directory through the existing
`CCAS_ACCOUNTS_ROOT` override, so no test reads a real token.

What must be pinned:

- `from_oauth` normalises the endpoint's ISO 8601 and 0–100 into the same
  reading shape the other two sources produce.
- `due()` is False for a reading fresher than the interval, True for an older
  one, and True when nothing is recorded.
- A 401, a timeout and a malformed body each record nothing and leave an
  existing reading intact.
- An expired token means no fetch is attempted at all.
- A successful poll whose numbers match the recorded ones does not write and
  does not signal.
- `poll.py` never opens a path under `claude_home()`, and the account's
  `.credentials.json` mtime is unchanged across a poll — the same canary shape
  as `test_relink_never_touches_mtimes_in_claude_home`.
