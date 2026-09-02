"""Fetching an account's usage limits without a session.

The statusline hook is the only source while a session runs, and there is no
session for the account you are *not* using — so its numbers freeze the moment
the last one closes. This asks the endpoint Claude Code itself asks,
`GET /api/oauth/usage`, with the token already sitting in the account's
credentials file. `docs/usage-limits-research.md` has the three routes that lost.

Two rules hold it up:

- **Read the credentials, never write them.** CCAS does not hold the refresh
  token to the fire itself: rotating one behind Claude Code's back risks that
  account's login and can invalidate the token a live session holds. An expired
  token is instead handed *back* to Claude Code — `claude mcp list` refreshes it
  on the way past, under its own cross-process lock — and the fresh one is read
  off disk. `CCAS_NO_TOKEN_REFRESH=1` takes that away again and restores the
  eight-hour horizon this had before, where an idle account simply went quiet.
- **Never fetch what the hook already knows.** `due()` skips an account recorded
  more recently than the interval, so the account being used stays the hook's
  and the timer serves the idle one.

No signalling here. The caller owns the bar, the same way `cmd_statusline` does.
"""
import email.utils
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import namedtuple

from . import accounts, paths, usage

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Seconds between polls of one account. The timer fires at this cadence too; the
# check is here as well because a hand-run `ccs poll` must not spend a request
# on numbers the hook wrote seconds ago.
INTERVAL = 300.0

# A token this close to expiry will not survive the round trip.
LEEWAY = 60.0

TIMEOUT = 5.0

# The longest a failure may hold the poll off. The wait doubles from INTERVAL
# and stops here: the account is expected to come back — a token is renewed, a
# rate limit lifts — and an hour is short enough that the bar is right again
# within one 5-hour window.
BACKOFF_CAP = 3600.0

OK, UNCHANGED, SKIPPED, EXPIRED, FAILED, NO_TOKEN = (
    "ok", "unchanged", "skipped", "expired", "failed", "no-token")

# `wrote` is what the signal rides on. It used to be "status is OK", which was
# the same thing while a reading was the only thing this module wrote; a stall
# is a change to what the label says too, and one that appears exactly when
# nothing else is arriving to repaint it.
Outcome = namedtuple("Outcome", "slug status detail wrote", defaults=(False,))

# What `fetch` answers. `retry_after` is the server's own word on when to come
# back, in seconds, or None — the third field rather than a code buried in the
# reason string, because a rate limiter that says when is the only party that
# knows, and the reason is written for a human to read in the journal.
Reply = namedtuple("Reply", "payload reason retry_after")

# The one failure that is about the token rather than about the endpoint.
DENIED = "http 401"


def access_token(slug: str):
    """`(token, expires_at)` in seconds, or None. Read-only, always."""
    path = paths.account_dir(slug) / ".credentials.json"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # The file nests under claudeAiOauth; tolerate a flat one rather than
    # assuming a shape CCAS does not own.
    oauth = blob.get("claudeAiOauth") or blob
    token, expires = oauth.get("accessToken"), oauth.get("expiresAt")
    if not token or not expires:
        return None
    return token, expires / 1000.0  # the file is milliseconds, CCAS is seconds


def renewal_wanted() -> bool:
    """The off switch, read at the point of use so a unit override takes hold
    without an install. `CCAS_NO_TOKEN_REFRESH=1` and the poll is back to what
    it was: an expired token is the end of the attempt."""
    return os.environ.get("CCAS_NO_TOKEN_REFRESH") != "1"


# What renews a token, measured 2026-07-28 against an account five hours past
# expiry. Not `claude auth status`: that reads the credentials and reports
# `loggedIn: true` over an expired token without touching it, which is how the
# first version of this shipped inert. A token is refreshed as a side effect of
# a **first-party API call**, which the bundle makes with `refreshOAuth: true`,
# and `mcp list` is the cheapest CLI command that makes one — about 2.5s, and no
# model quota. It is a side effect, so it is not promised: `renew()` re-reads
# rather than trusting it, and the poll degrades to the old eight-hour horizon
# the day a claude release stops doing it, visibly, in doctor's freshness row.
RENEW_ARGS = ("mcp", "list")
RENEW_TIMEOUT = 45.0


def ask_claude(slug: str, runner=None) -> None:
    """Run the renewing command under that account's `CLAUDE_CONFIG_DIR`.

    Nothing is read back from it — the answer is on disk, and `renew` looks
    there. `accounts.env_for` carries the `CCAS_INNER=1` guard.
    """
    runner = subprocess.run if runner is None else runner
    runner([str(paths.claude_bin()), *RENEW_ARGS], env=accounts.env_for(slug),
           capture_output=True, text=True, timeout=RENEW_TIMEOUT)


def renew(slug: str, asker=None):
    """Ask Claude Code to renew this account's token; re-read what it left.

    `(token, expires_at)` if a usable one is now on disk, else None. CCAS writes
    nothing here — claude refreshes the credentials for its own account, which
    is the whole point of routing through it rather than posting to the token
    endpoint ourselves.

    Never raises: the timer runs unattended, and a claude that is missing, slow
    or logged out is the same event as far as the caller is concerned — no fresh
    token this time.
    """
    asker = ask_claude if asker is None else asker
    try:
        asker(slug)
    except Exception:  # noqa: BLE001 — see the docstring
        return None
    return access_token(slug)


def backoff_delay(failures: int) -> float:
    """How long to wait after `failures` consecutive failures.

    The cadence, doubling: 5m, 10m, 20m, 40m, then an hour and no longer. It
    starts at INTERVAL because one failure is indistinguishable from a blip and
    the timer would have waited that long anyway — the point is what happens at
    the tenth, which before this was a tenth request into the same wall.
    """
    return min(INTERVAL * 2.0 ** max(0, failures - 1), BACKOFF_CAP)


def blocked_until(slug: str):
    """The earliest this account may be asked again, or None.

    Read off the stall rather than recomputed, because `retry_at` may be the
    server's `Retry-After` rather than our schedule.
    """
    record = usage.stall(slug)
    return None if record is None else record.get("retry_at")


def next_attempt(failures: int, now: float, retry_after=None) -> float:
    """When to come back: our schedule, or the server's, whichever is later.

    Later, not the server's outright. `Retry-After` is a rate limiter saying
    when *it* will answer again, which is a floor and not a ceiling — a 429
    answering "try in 5 seconds" to the fourth failure in a row would put the
    poll straight back to asking every five seconds. The schedule is also there
    to stop us, not only to obey them.
    """
    ours = now + backoff_delay(failures)
    theirs = None if retry_after is None else now + float(retry_after)
    return ours if theirs is None else max(ours, theirs)


def due(slug: str, now: float, interval: float = INTERVAL) -> bool:
    """Is this account's 5-hour window one the hook is not keeping up with?

    `usage.load`, not `usage.read`: the question is when the account last had a
    reading of its own, which is what the hook writes.

    Two tests, and the second one is not redundant. `fetched_at` says when the
    *file* last changed, and Claude Code names the two windows independently —
    a statusline tick carrying only `seven_day` writes, because seven_day moved,
    and `usage._carried` rightly keeps the stored 5-hour window rather than
    erasing it with silence. That write moves `fetched_at` without anybody
    having measured the 5-hour window at all. Read alone it says "the hook has
    this account", and on 2026-07-28 it locked the timer out of an account whose
    5-hour window had rolled over mid-session: the endpoint knew about the new
    window for twenty minutes and was never asked, the bar read idle throughout,
    and only a panel click — gated by `poll-stamp`, not by this — broke it.

    A recorded stall comes first, ahead of both: a failed fetch writes no
    reading at all, so neither test below can see that the last three attempts
    were refused, and the answer would be "due" at every tick for as long as the
    failure lasts. Measured on 2026-09-02 — 62 requests between midnight and
    06:25, 45 of them answered 429, into an endpoint that was telling us to stop.

    So a reading whose 5-hour window is not running is due whatever its
    timestamp says. It is the window the freshness is *about*: BOUNDED is the
    only state the hook can be said to be keeping up with, and an account that
    is genuinely idle was being polled at the interval anyway, because nothing
    was writing its file either.
    """
    blocked = blocked_until(slug)
    if blocked is not None and now < blocked:
        return False
    reading = usage.load(slug)
    if reading is None:
        return True
    if usage.state(reading, "five_hour", now).kind is not usage.BOUNDED:
        return True
    return (now - reading.get("fetched_at", 0.0)) >= interval


def age(seconds: float) -> str:
    """Short, for an outcome line and for doctor's freshness row."""
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds / 3600:.1f}h"


def retry_after(headers, now: float):
    """`Retry-After` in seconds, or None. Both spellings RFC 9110 allows.

    Never raises and never returns a negative: a clock skewed the wrong way
    against an HTTP date would otherwise read as "come back before you asked",
    which `next_attempt` would then take the max against and silently ignore —
    the same answer, arrived at by accident rather than on purpose.
    """
    try:
        value = headers.get("Retry-After") if headers else None
    except Exception:  # noqa: BLE001 — a header bag CCAS does not own
        return None
    if not value:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return None if when is None else max(0.0, when.timestamp() - now)


def fetch(token: str, opener=None, now=None) -> Reply:
    """A `Reply`: the payload, or a reason and what the server said about when.

    Everything that can go wrong here is the same event as far as the caller is
    concerned — no fresh reading this time — so the reason is a string for the
    user to read rather than an exception for the timer to trip over. The
    `Retry-After` is kept apart from it because it is the one part of a failure
    the caller can act on.
    """
    opener = urllib.request.urlopen if opener is None else opener
    now = time.time() if now is None else now
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return Reply(json.loads(response.read().decode("utf-8", "replace")),
                         "", None)
    except urllib.error.HTTPError as exc:
        return Reply(None, f"http {exc.code}",
                     retry_after(getattr(exc, "headers", None), now))
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return Reply(None, f"{type(exc).__name__}: {exc}", None)


def _stall(slug: str, status: str, detail: str, now: float, note: str = "",
           counts: bool = True, retry_at=None) -> Outcome:
    """Record why the poll could not answer, and say so.

    Every way out of `poll_account` that leaves the reading untouched comes
    through here, so there is one place that cannot forget: a stop nobody
    recorded is a stop `due()` cannot see and the label cannot say.
    """
    wrote = usage.record_stall(slug, status, now, retry_at=retry_at,
                               counts=counts, note=note)
    return Outcome(slug, status, detail, wrote)


def _failed(slug: str, reason: str, now: float, after=None) -> Outcome:
    """A failure that spent a request, with the wait it earns."""
    failures = (usage.stall(slug) or {}).get("failures", 0) + 1
    when = next_attempt(failures, now, after)
    return _stall(slug, FAILED, f"{reason} — reading unchanged; "
                  f"next attempt in {age(when - now)}", now, note=reason,
                  retry_at=when)


def poll_account(slug: str, now=None, fetcher=None, force=False,
                 renewer=None) -> Outcome:
    """One account: decide, renew if it must, fetch, record. Never raises."""
    now = time.time() if now is None else now
    fetcher = fetch if fetcher is None else fetcher

    if not force and not due(slug, now):
        blocked = blocked_until(slug)
        if blocked is not None and now < blocked:
            return Outcome(slug, SKIPPED, f"backing off — {age(blocked - now)} to go")
        recorded = (usage.load(slug) or {}).get("fetched_at", now)
        return Outcome(slug, SKIPPED, f"recorded {age(now - recorded)} ago")

    credentials = access_token(slug)
    if credentials is None:
        # Nothing was asked, so there is no failure to count and no wait to
        # grow: being logged out is a stop the endpoint had no part in.
        return _stall(slug, NO_TOKEN, "no usable credentials — is it logged in?",
                      now, counts=False)
    token, expires_at = credentials
    if expires_at <= now + LEEWAY:
        when = (f"expired {age(now - expires_at)} ago" if expires_at <= now
                else "expires within the minute")
        if not renewal_wanted():
            return _stall(slug, EXPIRED,
                          f"token {when} — no fetch; renewal is switched off",
                          now, counts=False)
        renewed = renew(slug, renewer)
        if renewed is None or renewed[1] <= now + LEEWAY:
            return _stall(slug, EXPIRED,
                          f"token {when} — claude did not renew it; is it logged in?",
                          now, counts=False)
        token, expires_at = renewed

    reply = fetcher(token)
    if reply.payload is None and reply.reason == DENIED and renewal_wanted():
        # The server's word against the file's. Renewal ran off the local expiry
        # alone until 2026-09-02, so a token the endpoint had stopped accepting
        # was re-sent every five minutes with ten hours still on its clock.
        # Only once, and only if claude actually left a different one: re-sending
        # the same rejected token is one more refused request for nothing.
        renewed = renew(slug, renewer)
        if renewed is not None and renewed[0] != token \
                and renewed[1] > now + LEEWAY:
            token = renewed[0]
            reply = fetcher(token)

    if reply.payload is None:
        return _failed(slug, reply.reason, now, reply.retry_after)
    reading = usage.from_oauth(reply.payload, now)
    if reading is None:
        return _failed(slug, "no windows in the response", now)
    # The endpoint answered, which is the whole of what a stall records.
    cleared = usage.clear_stall(slug)
    if usage.record_reading(slug, reading):
        return Outcome(slug, OK, usage.column(reading, "five_hour", now), True)
    return Outcome(slug, UNCHANGED, "same numbers", cleared)


# ── the on-demand poll ────────────────────────────────────────────────────────

# The shortest gap between two panel-driven fetches for one account. Separate
# from INTERVAL on purpose: that one answers "how often should this account be
# refreshed in the background", and this one answers "how often may a human
# asking spend a request". They are free to differ, and do.
PANEL_INTERVAL = 60.0


def asked_at(slug: str):
    """When this account last asked the endpoint, or None.

    Its own file, not a key in the reading. `usage.record` defines `fetched_at`
    as *when this reading was first seen* — the write-on-change rule leaves it
    alone while the numbers hold steady — so for an idle account whose usage is
    not moving it is permanently older than any interval, and `due()` built on
    it is permanently true. That is right for the timer, whose cadence is the
    point, and useless as a rate limit. This is the other fact: when we last
    asked, whatever the answer was.

    Unreadable reads as never asked. Failing open costs one request; failing
    shut would wedge an account's panel refresh on a stray byte.
    """
    try:
        return float((paths.account_dir(slug) / paths.POLL_STAMP_FILE)
                     .read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def stamp(slug: str, now: float) -> None:
    """Record that we asked. Never raises — a read-only account directory means
    no rate limiting, not a panel that fails to open."""
    try:
        path = paths.account_dir(slug) / paths.POLL_STAMP_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{now:.3f}\n", encoding="utf-8")
    except OSError:
        pass


def _never_renew(_slug: str) -> None:
    """The renewer the on-demand path passes: it asks claude nothing.

    Renewal is a process and up to RENEW_TIMEOUT seconds, and the five-minute
    timer has already done it by the time a panel is opened. `renew()` re-reads
    the credentials after calling this, finds the same expired token, and
    poll_account reports EXPIRED without spending a request.
    """


def poll_on_demand(slug: str, now=None, fetcher=None,
                   interval: float = PANEL_INTERVAL) -> Outcome:
    """One account, because someone opened the panel and is looking at it.

    The stamp is written **before** the fetch. A fetch that fails, or hangs for
    the full TIMEOUT, still counts as having asked — stamping on success only
    would turn a broken network into an unthrottled retry, one request per panel
    open, which is the precise behaviour the limit is here to prevent. The
    endpoint is rate limited on Anthropic's side (Claude Code's own bundle
    carries a `rateLimitedVia` fallback; `fetch` has none), so spending requests
    that cannot succeed is the worst thing this could do.

    `force=True` into poll_account, deliberately: the stamp above is this path's
    gate, and `due()` is the timer's. Letting both apply would put the 5-minute
    interval back in front of a question a human just asked. The backoff is the
    exception, checked here rather than left to `force` to skip — it is not a
    guess about whether the answer would be interesting, it is the endpoint
    having said no, and clicking twice does not change that answer.
    """
    now = time.time() if now is None else now
    blocked = blocked_until(slug)
    if blocked is not None and now < blocked:
        # A click cannot lift a rate limit. `force` below means "a human asked",
        # which is the right thing to say to the freshness gate and the wrong
        # thing to say to the endpoint's own answer of "not yet".
        return Outcome(slug, SKIPPED, f"backing off — {age(blocked - now)} to go")
    last = asked_at(slug)
    if last is not None and (now - last) < interval:
        return Outcome(slug, SKIPPED, f"asked {age(now - last)} ago")
    stamp(slug, now)
    return poll_account(slug, now, fetcher, force=True, renewer=_never_renew)


TIMER = "ccas-poll.timer"


def timer_state(runner=None) -> str:
    """systemd's own word for the timer, or "unknown" if it cannot be asked.

    A string rather than a bool: "inactive" and "failed" want different advice,
    and a machine without systemd is neither.
    """
    runner = subprocess.run if runner is None else runner
    try:
        proc = runner(["systemctl", "--user", "is-active", TIMER],
                      capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (getattr(proc, "stdout", "") or "").strip() or "unknown"


def poll(slugs, now=None, fetcher=None, force=False, renewer=None) -> list:
    """Every account, one outcome each. The timer runs this unattended, so one
    broken account may not stop the rest."""
    return [poll_account(slug, now, fetcher, force, renewer) for slug in slugs]
