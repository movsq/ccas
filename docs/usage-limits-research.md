# Reading an account's usage limits — what was measured

2026-07-25. Findings only; no design yet. Everything here was verified on the
real system or quoted from official docs. **Do not re-run the probes** — two of
them cost real quota, and the answers are below.

## What the numbers are

Claude Code tracks two windows per subscription account: a 5-hour one and a
7-day one, each a percentage plus a reset timestamp.

## Four ways to get them, and what each one actually does

### A — read `.claude.json` (works, stale)

`$CLAUDE_CONFIG_DIR/.claude.json` holds `cachedUsageUtilization`:

```json
{ "fetchedAtMs": 1784995…, "accountUuid": "…",
  "utilization": {
    "five_hour": { "utilization": 94, "resets_at": "2026-07-25T17:29:59Z" },
    "seven_day": { "utilization": 19, "resets_at": "2026-07-28T08:59:59Z" },
    "limits": [ { "kind": "session", "percent": 94, "severity": "normal", … } ] } }
```

Per account, since each account dir is its own `CLAUDE_CONFIG_DIR`. But it is an
**opportunistic cache**. Measured, by watching the key appear and not appear:

| action | writes the key? |
|---|---|
| `claude -p …` | no |
| plain interactive session start | no |
| `/usage` in a session | **yes** |

So a bar reading this shows whatever the last `/usage` left behind, and shows
nothing at all for an account where `/usage` was never run.

### B — drive `/usage` to refresh

Works (that is how the table above was produced — `expect`, a pty, `send
"/usage\r"`). Rejected: it spawns a real session per refresh and breaks the
moment that UI changes. The user's constraint was "nothing visible, no windows".

### C — call the API (works, but don't)

Extracted from the 2.1.220 bundle:

```js
fetchUtilization: GET /api/oauth/usage
Pi.get("/api/oauth/usage", { timeout: 5000,
       headers: {"Content-Type":"application/json"}, refreshOAuth: true })
```

Confirmed live: `GET https://api.anthropic.com/api/oauth/usage` with
`Authorization: Bearer <accessToken from .credentials.json>` → **HTTP 200**,
`{"five_hour":{"utilization":98.0,…},"seven_day":{"utilization":20.0,…}}`. No
`anthropic-beta` header and no special User-Agent were needed, and a single call
was not rate-limited — a research subagent claimed both were required and that
the endpoint "aggressively rate limits"; neither held up here.

**Rejected on policy, not on mechanics.** From
<https://code.claude.com/docs/en/legal-and-compliance>:

> **OAuth authentication** is intended exclusively for purchasers of Claude
> Free, Pro, Max, Team, and Enterprise subscription plans and is designed to
> support ordinary use of Claude Code and other native Anthropic applications.
> […] Anthropic does not permit third-party developers to offer Claude.ai login
> or to route requests through Free, Pro, or Max plan credentials on behalf of
> their users.

The explicit prohibition is aimed at developers routing *other people's*
requests, which CCAS is not. But "intended exclusively for ordinary use of
Claude Code" is not a green light, and taking an undocumented route to numbers
that a documented route also provides buys nothing.

Second reason to stay away even if policy allowed it: the bundle calls this with
`refreshOAuth: true`. Token refresh rotates credentials, and a botched write to
`.credentials.json` would break that account's login. CCAS must never refresh
and never write credentials.

That last sentence still holds for *CCAS doing it*. It does not mean the token
can never be renewed: since 2026-07-27 `poll.renew()` has claude renew it
(`poll.RENEW_ARGS`), which leaves the writing to the program that owns the file. See
`docs/why.md`, "The eight-hour horizon was a horizon, not a law".

### D — the statusline hook (documented, the one to build on)

Claude Code passes JSON on stdin to the statusline command, and it includes the
same numbers. Confirmed in the docs *and* in the binary's own inline comment:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 23.5, "resets_at": 1738425600 },
  "seven_day": { "used_percentage": 41.2, "resets_at": 1738857600 }
}
```

<https://code.claude.com/docs/en/statusline>:

> `rate_limits`: appears only for Claude.ai subscribers (Pro/Max) after the
> first API response in the session. Each window (`five_hour`, `seven_day`) may
> be independently absent.

`used_percentage` is 0–100 and `resets_at` is **unix epoch seconds** — note both
differ from the `.claude.json` cache, which stores 0–100 under `utilization` and
an ISO 8601 string. Two shapes for one fact; a reader must handle both.

## The CCAS-specific constraint

`statusLine` is configured in `settings.json` — and in every account directory
`settings.json` is a **symlink to `~/.claude/settings.json`**. Writing it would
break the never-write-to-`~/.claude` invariant, which is the whole design.

So CCAS cannot install the hook. It can ship the script and have `ccs doctor`
report whether the hook is wired up, leaving the one-line edit to the user —
the same stance CCAS already takes on `style.css`.

## Freshness, if D is built

The hook fires during interactive sessions only, after the first API response.
So the number is "as of your last session activity in that account" — good for
the account you are actually using, arbitrarily stale for one left idle. Any
display must carry its age, and fall back to the `.claude.json` cache (A) when
the hook has never run.

## Show the reset time, not the percentage

`resets_at` is an **absolute anchor**, not a rolling recompute. The same value
came back from three readings taken ~40 minutes apart:

```
~/.claude.json  fetched 17:03  five_hour.resets_at = 2026-07-25T17:30:00.266410Z
/usage probe    fetched 18:47  five_hour.resets_at = 2026-07-25T17:29:59.990326Z
API call        fetched 18:52  five_hour.resets_at = 2026-07-25T17:29:59.990326Z
```

That is what rescues the whole feature from the freshness problem above. A
percentage decays in value the moment it is written; a reset time does not:

- **past** → the window has rolled over since the reading, so the old ceiling is
  gone. Knowable with no fetch at all.
- **future** → the reading is still inside the current window, so its percentage
  is a valid *lower bound* — usage only climbs within a window. "≥94%, clears at
  19:30" is honest and actionable from a stale cache.

So the reset time is the headline and the percentage is the qualifier, not the
other way round. Note the two sources disagree on format: the statusline gives
unix epoch seconds, `.claude.json` gives an ISO 8601 string.

One consequence to design for: if the 5-hour window is anchored to first use
rather than to fixed clock slots, then once it has rolled over there is no next
`resets_at` until the account is used again. The display for that state is
"open", not a time — and that is the *good* state, so it should not read like
missing data.
