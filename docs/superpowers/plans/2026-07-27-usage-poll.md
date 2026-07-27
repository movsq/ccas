# Usage polling — implementation plan

> **For agentic workers:** the user of this repo has explicitly declined
> subagent-driven development. Execute this plan **inline**, task by task, in
> the session that holds it. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an account's quota numbers keep moving when no Claude Code session is
running, on a cadence the user controls.

**Architecture:** a new `ccas/poll.py` reads an account's access token and GETs
`/api/oauth/usage`, the endpoint Claude Code itself uses; `usage.py` learns that
payload as a third input shape and records it through the existing
write-only-on-change path; a systemd user timer runs `ccs poll` every five
minutes. Nothing about the display changes — the same tokens, the same ramp, the
same signal riding on a write.

**Tech stack:** Python 3.14 stdlib only (`urllib.request`, `json`), pytest,
bash, systemd user units.

**Spec:** `docs/superpowers/specs/2026-07-27-usage-poll-design.md`. Read it
first; the rejected alternatives there answer most "why not just…" questions.

## Global constraints

- **Python 3.14, stdlib only at runtime.** No `requests`, no new dependency.
  HTTP is `urllib.request`.
- **Never write to `~/.claude`.** Read only. The canary is
  `test_relink_never_touches_mtimes_in_claude_home`; this feature adds its own.
- **Never write `.credentials.json`, and never refresh a token.** Read the
  access token, use it, and treat expiry as "no fetch" — never as "renew".
- **Tests must never touch real state or open a socket.** Every path goes
  through `ccas/paths.py` env overrides; every network call goes through an
  injected fetcher.
- **Never delete.** Replaced files move to `~/.claude_trash`.
- **TDD:** failing test → verify it fails → implement → verify it passes →
  commit. One commit per task.
- **Never co-sign or co-author a commit.**
- Run the full suite with `python -m pytest` (~330 tests, under a second).
- `ccs` runs the **installed** copy. Re-run `./install.sh` before any live check.

---

### Task 1: `usage.py` learns the endpoint's shape

**Files:**
- Modify: `ccas/usage.py` (add `from_oauth`, split `record`)
- Test: `tests/test_usage.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `usage.from_oauth(payload: dict, now: float) -> dict | None` — a normalised
    reading with `source == "oauth"`, or None when the payload names neither
    window.
  - `usage.record_reading(slug: str, reading: dict | None) -> bool` — True when
    it wrote.
  - `usage.record(slug, payload, now=None) -> bool` — unchanged signature and
    behaviour; now a thin wrapper over `record_reading`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_usage.py`, after the existing "the two input shapes" block:

```python
def oauth(five=(3.0, "2026-07-27T02:39:59.757453+00:00"),
          seven=(0.0, "2026-08-02T18:59:59.757477+00:00")):
    """The /api/oauth/usage body, as measured on 2026-07-27."""
    body = {}
    if five:
        body["five_hour"] = {"utilization": five[0], "resets_at": five[1],
                             "limit_dollars": None}
    if seven:
        body["seven_day"] = {"utilization": seven[0], "resets_at": seven[1]}
    body["seven_day_opus"] = None          # the real body carries several of these
    return body


def test_the_endpoint_is_a_third_shape_of_the_same_fact():
    """The cache's shape without the cachedUsageUtilization wrapper: 0-100 under
    `utilization`, an ISO 8601 resets_at. Only this module sees any of the three.
    """
    reading = usage.from_oauth(oauth(), now=1785104289.0)
    assert reading["five_hour"] == {"percent": 3.0, "resets_at": 1785120000}
    assert reading["seven_day"]["percent"] == 0.0
    assert reading["source"] == "oauth"
    assert reading["fetched_at"] == 1785104289.0


def test_an_endpoint_window_may_be_independently_absent():
    reading = usage.from_oauth(oauth(seven=None), now=1.0)
    assert reading["five_hour"] is not None
    assert reading["seven_day"] is None


def test_an_endpoint_body_naming_neither_window_is_not_a_reading():
    """A body of nulls must not erase a good reading — the same rule the hook
    payload gets."""
    assert usage.from_oauth({}, now=1.0) is None
    assert usage.from_oauth({"seven_day_opus": None}, now=1.0) is None
    assert usage.from_oauth(None, now=1.0) is None


def test_record_reading_writes_only_a_change(monkeypatch, tmp_path):
    """The rule the hook path already obeys, now reachable with an
    already-normalised reading: the poll must not write four times an hour for
    numbers that did not move."""
    env(monkeypatch, tmp_path)
    reading = usage.from_oauth(oauth(), now=100.0)
    assert usage.record_reading("work", reading) is True
    assert usage.record_reading("work", usage.from_oauth(oauth(), now=200.0)) is False
    moved = usage.from_oauth(oauth(five=(9.0, "2026-07-27T02:39:59.757453+00:00")),
                             now=300.0)
    assert usage.record_reading("work", moved) is True
    assert usage.load("work")["five_hour"]["percent"] == 9.0


def test_record_reading_ignores_a_non_reading(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    usage.record_reading("work", usage.from_oauth(oauth(), now=100.0))
    assert usage.record_reading("work", None) is False
    assert usage.load("work") is not None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_usage.py -k "endpoint or record_reading" -v`
Expected: FAIL — `AttributeError: module 'ccas.usage' has no attribute 'from_oauth'`.

- [ ] **Step 3: Implement**

In `ccas/usage.py`, add after `from_cache`:

```python
def from_oauth(payload: dict, now: float):
    """The `/api/oauth/usage` body — the third shape of the same fact.

    Same spelling as the cache (0-100 under `utilization`, an ISO 8601
    `resets_at`) without the `cachedUsageUtilization` wrapper, because this is
    what Claude Code stores *into* that key. The real body also carries
    `seven_day_opus` and friends as nulls; naming neither of the two windows we
    read makes it a non-reading, not an empty one.
    """
    payload = payload or {}
    return _reading("oauth", now, {
        key: _window((payload.get(key) or {}).get("utilization"),
                     _epoch((payload.get(key) or {}).get("resets_at")))
        for key in WINDOWS})
```

Replace the body of `record()` with a split, keeping the existing docstring on
`record`:

```python
def record_reading(slug: str, reading) -> bool:
    """Write an already-normalised reading if it says something new.

    The half of `record` that does not care which source produced the reading —
    the poll reaches the same write-on-change rule as the hook, and with it the
    signal that rides on a write.
    """
    if reading is None:
        return False  # a payload with no windows must not erase a good one
    if _same(load(slug), reading):
        return False
    _atomic_write(paths.account_dir(slug) / paths.USAGE_FILE,
                  json.dumps(reading, indent=2) + "\n")
    return True


def record(slug: str, payload: dict, now=None) -> bool:
    """<keep the existing docstring here verbatim>"""
    now = time.time() if now is None else now
    return record_reading(slug, from_statusline(payload, now))
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, including every pre-existing `record()` test — the split must
not change hook behaviour.

- [ ] **Step 5: Commit**

```bash
git add ccas/usage.py tests/test_usage.py
git commit -m "Teach usage.py the endpoint's shape, and split record()

from_oauth joins from_statusline and from_cache: nothing outside this module
parses a source shape. record_reading is the half the poll needs — the same
write-only-on-change rule, reached with a reading rather than a hook payload."
```

---

### Task 2: `poll.py` — credentials, freshness, orchestration

The network call is Task 3; this task is everything around it, with the fetcher
injected. That boundary is what keeps the tests off the network.

**Files:**
- Create: `ccas/poll.py`
- Test: `tests/test_poll.py` (create)

**Interfaces:**
- Consumes: `usage.from_oauth`, `usage.record_reading`, `usage.load` (Task 1).
- Produces:
  - `poll.INTERVAL = 300.0`, `poll.LEEWAY = 60.0`
  - `poll.OK, SKIPPED, EXPIRED, FAILED, NO_TOKEN, UNCHANGED` — status strings.
  - `poll.Outcome = namedtuple("Outcome", "slug status detail")`
  - `poll.access_token(slug: str) -> tuple[str, float] | None` — `(token,
    expires_at_epoch_seconds)`.
  - `poll.due(slug: str, now: float, interval: float = INTERVAL) -> bool`
  - `poll.poll_account(slug, now=None, fetcher=None, force=False) -> Outcome`
  - `poll.poll(slugs, now=None, fetcher=None, force=False) -> list[Outcome]`

  `fetcher` has the signature `fetcher(token: str) -> tuple[dict | None, str]`
  — `(payload, "")` on success, `(None, reason)` on failure. Task 3 supplies the
  default.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_poll.py`:

```python
"""The poll: fetching usage for an account with no session running.

No test here opens a socket — `poll_account` takes its fetcher as a parameter,
and every case below passes a stub. No test reads a real credentials file
either: CCAS_ACCOUNTS_ROOT points at tmp_path.
"""
import importlib
import json

import ccas.paths as paths
import ccas.poll as poll
import ccas.usage as usage


def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_NO_RELOAD", "1")
    importlib.reload(paths)
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / "accts" / "work").mkdir(parents=True, exist_ok=True)
    return tmp_path


def credentials(tmp_path, expires_at_ms, token="sk-tok"):
    path = tmp_path / "accts" / "work" / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": token, "refreshToken": "sk-ref",
        "expiresAt": expires_at_ms, "subscriptionType": "pro"}}))
    return path


def body(five=3.0):
    return {"five_hour": {"utilization": five,
                          "resets_at": "2026-07-27T02:39:59.757453+00:00"},
            "seven_day": {"utilization": 0.0,
                          "resets_at": "2026-08-02T18:59:59.757477+00:00"}}


def answering(payload, error=""):
    """A fetcher that answers without a socket, recording the token it saw."""
    seen = []

    def fetcher(token):
        seen.append(token)
        return payload, error
    fetcher.seen = seen
    return fetcher


NOW = 1785104289.0


# ── the credential read ───────────────────────────────────────────────────────

def test_access_token_reads_the_account_and_converts_the_expiry(monkeypatch, tmp_path):
    """expiresAt is milliseconds in the file and seconds everywhere in CCAS."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=1785142943632)
    assert poll.access_token("work") == ("sk-tok", 1785142943.632)


def test_access_token_is_none_when_there_is_nothing_usable(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.access_token("work") is None            # no file at all
    (tmp_path / "accts" / "work" / ".credentials.json").write_text("{ not json")
    assert poll.access_token("work") is None
    (tmp_path / "accts" / "work" / ".credentials.json").write_text('{"claudeAiOauth": {}}')
    assert poll.access_token("work") is None


def test_polling_never_writes_the_credentials_file(monkeypatch, tmp_path):
    """The rule the whole feature rests on: read the token, never renew it.
    A rotation risks the account's login and can invalidate the token a live
    session is holding."""
    env(monkeypatch, tmp_path)
    path = credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    before = path.stat().st_mtime_ns
    poll.poll_account("work", now=NOW, fetcher=answering(body()))
    assert path.stat().st_mtime_ns == before
    assert json.loads(path.read_text())["claudeAiOauth"]["accessToken"] == "sk-tok"


# ── freshness ─────────────────────────────────────────────────────────────────

def test_due_is_false_while_the_hook_is_keeping_up(monkeypatch, tmp_path):
    """The account you are working in is served by the statusline hook. Polling
    it would spend a request on numbers that arrived seconds ago."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    assert poll.due("work", now=NOW + 12) is False
    assert poll.due("work", now=NOW + 301) is True


def test_due_is_true_when_nothing_has_ever_been_recorded(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.due("work", now=NOW) is True


def test_a_fresh_account_is_skipped_without_touching_the_network(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW + 10, fetcher=fetcher)
    assert outcome.status == poll.SKIPPED
    assert fetcher.seen == []


def test_force_polls_a_fresh_account_anyway(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    fetcher = answering(body())
    poll.poll_account("work", now=NOW + 10, fetcher=fetcher, force=True)
    assert fetcher.seen == ["sk-tok"]


# ── the expiry horizon ────────────────────────────────────────────────────────

def test_an_expired_token_is_not_fetched_with_and_not_renewed(monkeypatch, tmp_path):
    """A token lives ~8h and only Claude Code renews it. Past that the account
    goes quiet until it is next used — deliberately, because renewing is the one
    thing this must never do."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW, fetcher=fetcher)
    assert outcome.status == poll.EXPIRED
    assert fetcher.seen == []


def test_a_token_inside_the_leeway_counts_as_expired(monkeypatch, tmp_path):
    """A token with thirty seconds left will not survive the request."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 30) * 1000))
    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body()))
    assert outcome.status == poll.EXPIRED


def test_a_missing_credentials_file_reports_rather_than_raises(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.poll_account("work", now=NOW,
                             fetcher=answering(body())).status == poll.NO_TOKEN


# ── recording what came back ──────────────────────────────────────────────────

def test_a_successful_poll_records_the_reading(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    assert outcome.status == poll.OK
    recorded = usage.load("work")
    assert recorded["five_hour"]["percent"] == 41.0
    assert recorded["source"] == "oauth"


def test_a_poll_that_finds_the_same_numbers_does_not_write(monkeypatch, tmp_path):
    """Write-on-change is what lets the bar's signal ride on the write."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body()))
    path = tmp_path / "accts" / "work" / "usage.json"
    before = path.stat().st_mtime_ns
    outcome = poll.poll_account("work", now=NOW + 400, fetcher=answering(body()))
    assert outcome.status == poll.UNCHANGED
    assert path.stat().st_mtime_ns == before


def test_a_failed_fetch_leaves_the_previous_reading_standing(monkeypatch, tmp_path):
    """No new bar state for failures: the last reading is still honest, because
    resets_at is an absolute anchor."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    outcome = poll.poll_account("work", now=NOW + 400,
                                fetcher=answering(None, "http 401"))
    assert outcome.status == poll.FAILED
    assert "401" in outcome.detail
    assert usage.load("work")["five_hour"]["percent"] == 41.0


def test_a_body_with_no_windows_is_a_failure_not_an_erasure(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    poll.poll_account("work", now=NOW + 400, fetcher=answering({"seven_day_opus": None}))
    assert usage.load("work")["five_hour"]["percent"] == 41.0


def test_poll_returns_one_outcome_per_slug_and_never_raises(monkeypatch, tmp_path):
    """One broken account must not stop the others: the timer runs unattended."""
    env(monkeypatch, tmp_path)
    (tmp_path / "accts" / "other").mkdir()
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    outcomes = poll.poll(["work", "other"], now=NOW, fetcher=answering(body()))
    assert [o.slug for o in outcomes] == ["work", "other"]
    assert outcomes[0].status == poll.OK
    assert outcomes[1].status == poll.NO_TOKEN


def test_polling_never_touches_claude_home(monkeypatch, tmp_path):
    """The invariant, checked around this command the way CLAUDE.md asks."""
    env(monkeypatch, tmp_path)
    home = tmp_path / "claude"
    (home / "settings.json").write_text("{}")
    before = {p.name: p.stat().st_mtime_ns for p in home.iterdir()}
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll(["work"], now=NOW, fetcher=answering(body()))
    assert {p.name: p.stat().st_mtime_ns for p in home.iterdir()} == before
    assert [p for p in home.iterdir() if p.is_symlink()] == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_poll.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'ccas.poll'`.

- [ ] **Step 3: Implement**

Create `ccas/poll.py`. Leave `fetch` out for now — Task 3 adds it — and let
`poll_account` require its fetcher until then:

```python
"""Fetching an account's usage limits without a session.

The statusline hook is the only source while a session runs, and there is no
session for the account you are *not* using — so its numbers freeze the moment
the last one closes. This asks the endpoint Claude Code itself asks,
`GET /api/oauth/usage`, with the token already sitting in the account's
credentials file. `docs/superpowers/specs/2026-07-27-usage-poll-design.md` has
why that route, and `docs/usage-limits-research.md` has the three that lost.

Two rules hold it up:

- **Read the credentials, never write them.** No refresh, ever: rotating a token
  risks that account's login, and can invalidate the one a live session holds.
  An expired token means no fetch — never a renewal. The horizon that buys is
  about eight hours, and it is the right trade: an account idle that long has
  rolled its five-hour window over, which `usage.state()` reports from the
  timestamp alone with no fetch at all.
- **Never fetch what the hook already knows.** `due()` skips an account recorded
  more recently than the interval, so the account being used stays the hook's
  and the timer serves the idle one.

No signalling here. The caller owns the bar, the same way `cmd_statusline` does.
"""
import json
import time
from collections import namedtuple

from . import paths, usage

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Seconds between polls of one account. The timer fires at this cadence too; the
# check is here as well because a hand-run `ccs poll` must not spend a request
# on numbers the hook wrote seconds ago.
INTERVAL = 300.0

# A token this close to expiry will not survive the round trip.
LEEWAY = 60.0

TIMEOUT = 5.0

OK, UNCHANGED, SKIPPED, EXPIRED, FAILED, NO_TOKEN = (
    "ok", "unchanged", "skipped", "expired", "failed", "no-token")

Outcome = namedtuple("Outcome", "slug status detail")


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


def due(slug: str, now: float, interval: float = INTERVAL) -> bool:
    """Has it been long enough since this account last recorded anything?

    `usage.load`, not `usage.read`: the question is when the account last had a
    reading of its own, which is what the hook writes.
    """
    reading = usage.load(slug)
    if reading is None:
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


def poll_account(slug: str, now=None, fetcher=None, force=False) -> Outcome:
    """One account: decide, fetch, record. Reports; never raises."""
    now = time.time() if now is None else now
    fetcher = fetch if fetcher is None else fetcher

    if not force and not due(slug, now):
        recorded = (usage.load(slug) or {}).get("fetched_at", now)
        return Outcome(slug, SKIPPED, f"recorded {age(now - recorded)} ago")

    credentials = access_token(slug)
    if credentials is None:
        return Outcome(slug, NO_TOKEN, "no usable credentials — is it logged in?")
    token, expires_at = credentials
    if expires_at <= now + LEEWAY:
        when = (f"expired {age(now - expires_at)} ago" if expires_at <= now
                else "expires within the minute")
        return Outcome(slug, EXPIRED, f"token {when} — no fetch, and CCAS never renews")

    payload, error = fetcher(token)
    if payload is None:
        return Outcome(slug, FAILED, f"{error} — reading unchanged")
    reading = usage.from_oauth(payload, now)
    if reading is None:
        return Outcome(slug, FAILED, "no windows in the response — reading unchanged")
    if usage.record_reading(slug, reading):
        return Outcome(slug, OK, usage.column(reading, "five_hour", now))
    return Outcome(slug, UNCHANGED, "same numbers")


def poll(slugs, now=None, fetcher=None, force=False) -> list:
    """Every account, one outcome each. The timer runs this unattended, so one
    broken account may not stop the rest."""
    return [poll_account(slug, now, fetcher, force) for slug in slugs]
```

Note `poll_account` references `fetch` before Task 3 defines it — that name is
only resolved when `fetcher` is None, which no test in this task does. Task 3
adds the function.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_poll.py -v && python -m pytest`
Expected: all of `test_poll.py` PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add ccas/poll.py tests/test_poll.py
git commit -m "Add poll.py: the credential read, the freshness gate, the outcome

Everything around the request, with the fetcher injected so no test opens a
socket. Two rules are pinned by their own tests: the credentials file is never
written, and an account the hook is already keeping fresh is never fetched for."
```

---

### Task 3: the request itself

**Files:**
- Modify: `ccas/poll.py` (add `fetch`)
- Test: `tests/test_poll.py`

**Interfaces:**
- Consumes: `poll.USAGE_URL`, `poll.TIMEOUT` (Task 2).
- Produces: `poll.fetch(token: str, opener=None) -> tuple[dict | None, str]`.
  `opener` has `urllib.request.urlopen`'s signature and defaults to it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_poll.py`:

```python
# ── the request ───────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_asks_the_endpoint_claude_code_asks():
    """URL, bearer token and timeout, exactly as measured from the 2.1.220
    bundle. No anthropic-beta header and no special user-agent — the research
    doc confirmed live that neither is needed."""
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeResponse(body())

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert seen["url"] == "https://api.anthropic.com/api/oauth/usage"
    assert seen["auth"] == "Bearer sk-tok"
    assert seen["timeout"] == poll.TIMEOUT
    assert error == ""
    assert payload["five_hour"]["utilization"] == 3.0


def test_fetch_turns_an_http_error_into_a_reason():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(poll.USAGE_URL, 401, "Unauthorized", {}, None)

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert payload is None
    assert "401" in error


def test_fetch_turns_a_timeout_into_a_reason():
    def opener(request, timeout=None):
        raise TimeoutError("timed out")

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert payload is None
    assert error


def test_fetch_survives_a_body_that_is_not_json():
    class Garbage(FakeResponse):
        def read(self):
            return b"<html>nope"

    payload, error = poll.fetch("sk-tok", opener=lambda r, timeout=None: Garbage(None))
    assert payload is None
    assert error
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_poll.py -k fetch -v`
Expected: FAIL — `AttributeError: module 'ccas.poll' has no attribute 'fetch'`.

- [ ] **Step 3: Implement**

Add to `ccas/poll.py` — the import at the top with the others:

```python
import urllib.error
import urllib.request
```

and the function, above `poll_account`:

```python
def fetch(token: str, opener=None):
    """`(payload, "")`, or `(None, reason)`. Never raises.

    Everything that can go wrong here is the same event as far as the caller is
    concerned — no fresh reading this time — so the reason is a string for the
    user to read rather than an exception for the timer to trip over.
    """
    opener = urllib.request.urlopen if opener is None else opener
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        return None, f"http {exc.code}"
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return None, f"{type(exc).__name__}: {exc}"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_poll.py -v && python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/poll.py tests/test_poll.py
git commit -m "The request: urllib, five seconds, bearer token, no exceptions out

Every failure is the same event to the caller — no fresh reading — so it comes
back as a reason to print rather than something for the timer to trip over."
```

---

### Task 4: `ccs poll`

**Files:**
- Modify: `ccas/cli.py` (add `cmd_poll`, dispatch entry)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `poll.poll`, `poll.Outcome`, the status constants (Tasks 2–3).
- Produces: `cli.cmd_poll(args: list) -> int`, reachable as `ccs poll [<slug>]
  [--force]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`, following that file's existing fixture style:

```python
def test_poll_signals_only_the_accounts_that_changed(monkeypatch, tmp_path, capsys):
    """The signal rides on the write, exactly as it does for the hook: a poll
    that finds the same numbers must not repaint anything."""
    reg = env_with_accounts(monkeypatch, tmp_path)   # existing helper in this file
    sent = []
    monkeypatch.setattr(cli.waybar, "signal", lambda n: sent.append(n))
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(slugs[0], cli.poll.OK, "5h ≥41%"),
        cli.poll.Outcome(slugs[1], cli.poll.UNCHANGED, "same numbers")])

    assert cli.main(["poll"]) == 0
    assert sent == [reg["accounts"][0]["signal"]]
    out = capsys.readouterr().out
    assert "5h ≥41%" in out and "same numbers" in out


def test_poll_takes_one_slug(monkeypatch, tmp_path):
    env_with_accounts(monkeypatch, tmp_path)
    asked = {}
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: asked.setdefault("slugs", slugs) or [])
    cli.main(["poll", "work"])
    assert asked["slugs"] == ["work"]


def test_poll_passes_force_through(monkeypatch, tmp_path):
    env_with_accounts(monkeypatch, tmp_path)
    asked = {}
    monkeypatch.setattr(cli.poll, "poll",
                        lambda slugs, **kw: asked.update(kw) or [])
    cli.main(["poll", "--force"])
    assert asked["force"] is True


def test_poll_fails_only_when_every_account_failed(monkeypatch, tmp_path):
    """A timer that reports failure for one dead account would cry wolf in the
    journal every five minutes."""
    env_with_accounts(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.waybar, "signal", lambda n: None)
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(slugs[0], cli.poll.OK, ""),
        cli.poll.Outcome(slugs[1], cli.poll.FAILED, "http 401")])
    assert cli.main(["poll"]) == 0

    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(s, cli.poll.FAILED, "http 401") for s in slugs])
    assert cli.main(["poll"]) == 1


def test_poll_does_not_open_the_panel(monkeypatch, tmp_path):
    """`ccs poll` runs from a systemd timer with no display at all."""
    env_with_accounts(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [])
    monkeypatch.setattr(cli, "cmd_panel", lambda *a, **k: pytest.fail("opened a panel"))
    assert cli.main(["poll"]) == 0
```

If `test_cli.py` has no `env_with_accounts` helper, use whatever that file
already uses to build a registry in `tmp_path` — read the top of the file and
follow it rather than inventing a second convention.

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_cli.py -k poll -v`
Expected: FAIL — `AttributeError: module 'ccas.cli' has no attribute 'poll'`.

- [ ] **Step 3: Implement**

In `ccas/cli.py`, add `poll` to the package import line, then add the command
next to `cmd_usage`:

```python
def cmd_poll(args) -> int:
    """Fetch each account's usage from the endpoint and record what changed.

    What the systemd timer runs every five minutes, and what the user runs by
    hand to see it work without waiting. The signal rides on the write, the same
    rule `cmd_statusline` obeys — the bar repaints only when the numbers moved.
    """
    force = "--force" in args
    rest = [a for a in args if a != "--force"]
    reg = registry.load()
    accounts_ = [registry.find(reg, rest[0])] if rest else reg["accounts"]
    if accounts_ == [None]:
        print(f"ccs poll: unknown account: {rest[0]}", file=sys.stderr)
        return 1

    outcomes = poll.poll([a["slug"] for a in accounts_], force=force)
    for outcome in outcomes:
        print(f"{outcome.slug:<14} {outcome.status:<10} {outcome.detail}")
        if outcome.status == poll.OK:
            account = registry.find(reg, outcome.slug)
            if account:
                waybar.signal(account["signal"])
    # Not "any failed": one logged-out account must not make the timer look
    # broken in the journal every five minutes.
    return 1 if outcomes and all(o.status == poll.FAILED for o in outcomes) else 0
```

And in `main()`'s dispatch, beside the `usage` entry:

```python
    if command == "poll":
        return cmd_poll(rest)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_cli.py -k poll -v && python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ccas/cli.py tests/test_cli.py
git commit -m "ccs poll: the command the timer runs, and the one you run by hand

One line per account, and the module signal rides on a write exactly as it does
for the statusline hook. Exit 1 only when every account failed — a single
logged-out account must not cry wolf in the journal every five minutes."
```

---

### Task 5: the systemd units

**Files:**
- Create: `assets/ccas-poll.service`, `assets/ccas-poll.timer`
- Modify: `install.sh`, `uninstall.sh`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: `ccs poll` (Task 4).
- Produces: `~/.config/systemd/user/ccas-poll.{service,timer}`, overridable for
  tests with `CCAS_SYSTEMD_DIR`; `CCAS_SKIP_SYSTEMD=1` suppresses the
  `systemctl` calls.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install.py`:

```python
def test_install_writes_the_poll_units(tmp_path):
    """The timer is how the user controls the cadence — systemctl, not a CCAS
    setting. It must land installed, with the absolute ccs path baked in."""
    env = _sandbox_env(tmp_path)          # the dict the idempotency test builds
    env["CCAS_SYSTEMD_DIR"] = str(tmp_path / "systemd")
    env["CCAS_SKIP_SYSTEMD"] = "1"        # never enable a unit from a test
    (tmp_path / "claude").mkdir(exist_ok=True)
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')

    proc = subprocess.run(["bash", str(ROOT / "install.sh")], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    service = (tmp_path / "systemd" / "ccas-poll.service").read_text()
    timer = (tmp_path / "systemd" / "ccas-poll.timer").read_text()
    assert f"ExecStart={tmp_path}/bin/ccs poll" in service
    assert "@CCS@" not in service, "the placeholder must be substituted"
    assert "OnUnitActiveSec=5min" in timer
    assert "WantedBy=timers.target" in timer


def test_reinstall_trashes_the_previous_unit_rather_than_overwriting(tmp_path):
    """Never delete — the global rule, and these are files a user may have
    edited to retime the poll."""
    env = _sandbox_env(tmp_path)
    env["CCAS_SYSTEMD_DIR"] = str(tmp_path / "systemd")
    env["CCAS_SKIP_SYSTEMD"] = "1"
    (tmp_path / "claude").mkdir(exist_ok=True)
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / "ccas-poll.timer").write_text("# mine\n")

    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True)
    trashed = list((tmp_path / "trash").glob("ccas-poll.timer-*"))
    assert trashed and trashed[0].read_text() == "# mine\n"


def test_uninstall_trashes_the_units(tmp_path):
    env = _sandbox_env(tmp_path)
    env["CCAS_SYSTEMD_DIR"] = str(tmp_path / "systemd")
    env["CCAS_SKIP_SYSTEMD"] = "1"
    (tmp_path / "claude").mkdir(exist_ok=True)
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True)
    subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=env, check=True,
                   capture_output=True)
    assert not (tmp_path / "systemd" / "ccas-poll.timer").exists()
    assert list((tmp_path / "trash").glob("ccas-poll.timer-*"))
```

If `test_install.py` has no `_sandbox_env` helper, extract the env dict from
`test_install_is_idempotent_in_a_sandbox` into one and use it in all four tests
— the same dict, built once.

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_install.py -v`
Expected: FAIL — the unit files are not written.

- [ ] **Step 3: Implement**

Create `assets/ccas-poll.service`:

```ini
# Installed by CCAS's install.sh. @CCS@ is substituted with the ccs path.
[Unit]
Description=CCAS — record each Claude account's usage limits
Documentation=https://github.com/movsq/ccas

[Service]
Type=oneshot
ExecStart=@CCS@ poll
```

Create `assets/ccas-poll.timer`:

```ini
# Installed by CCAS's install.sh.
#
# This file is where the cadence lives — there is deliberately no CCAS setting
# for it. Retime with `systemctl --user edit ccas-poll.timer`, stop it with
# `systemctl --user stop ccas-poll.timer`, read failures with
# `journalctl --user -u ccas-poll`.
[Unit]
Description=Poll each Claude account's usage limits every five minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

In `install.sh`, after the `menu.css` block and before the `ccs relink` line:

```bash
# The poll timer. Unlike menu.css these are CCAS's files and are rewritten on
# every install — but never overwritten in place: a user who retimed the poll
# gets their version back out of the trash.
SYSTEMD_DIR="${CCAS_SYSTEMD_DIR:-$HOME/.config/systemd/user}"
mkdir -p "$SYSTEMD_DIR"
for unit in ccas-poll.service ccas-poll.timer; do
  if [ -e "$SYSTEMD_DIR/$unit" ]; then
    mv "$SYSTEMD_DIR/$unit" "$TRASH/$unit-$(date +%Y%m%d-%H%M%S)-$$"
  fi
  sed "s|@CCS@|$BIN_DIR/ccs|g" "$SRC/assets/$unit" > "$SYSTEMD_DIR/$unit"
done
if [ -z "${CCAS_SKIP_SYSTEMD:-}" ] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
  systemctl --user enable --now ccas-poll.timer || true
fi
```

In `uninstall.sh`, before the `killall` block:

```bash
SYSTEMD_DIR="${CCAS_SYSTEMD_DIR:-$HOME/.config/systemd/user}"
if [ -z "${CCAS_SKIP_SYSTEMD:-}" ] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now ccas-poll.timer 2>/dev/null || true
fi
for unit in ccas-poll.service ccas-poll.timer; do
  if [ -e "$SYSTEMD_DIR/$unit" ]; then
    mv "$SYSTEMD_DIR/$unit" "$TRASH/$unit-$STAMP"
  fi
done
```

Also add the echo line at the end of `install.sh`, next to the existing ones:

```bash
echo "Usage poll: systemctl --user status ccas-poll.timer"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_install.py -v && python -m pytest`
Expected: PASS. Then confirm the sandbox left no real unit behind:
`systemctl --user list-timers ccas-poll.timer` — nothing yet, because the tests
set `CCAS_SKIP_SYSTEMD=1` and a scratch `CCAS_SYSTEMD_DIR`.

- [ ] **Step 5: Commit**

```bash
git add assets/ccas-poll.service assets/ccas-poll.timer install.sh uninstall.sh tests/test_install.py
git commit -m "Install a systemd user timer that runs ccs poll every five minutes

The cadence lives in the unit and nowhere else: systemctl stops it, edits it and
explains it, which is the control the feature was asked for. An existing unit
goes to the trash rather than being overwritten — a user may have retimed it."
```

---

### Task 6: `ccs doctor` sees the poller

**Files:**
- Modify: `ccas/doctor.py`, `ccas/poll.py` (add `timer_state`)
- Test: `tests/test_doctor.py`, `tests/test_poll.py`

**Interfaces:**
- Consumes: `poll.access_token`, `usage.load` (Tasks 1–2).
- Produces: `poll.timer_state(runner=None) -> str` — systemd's word (`active`,
  `inactive`, `failed`) or `"unknown"` when systemd cannot be asked.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_poll.py`:

```python
def test_timer_state_reports_systemds_word():
    class Proc:
        stdout = "active\n"

    assert poll.timer_state(runner=lambda *a, **kw: Proc()) == "active"


def test_timer_state_is_unknown_when_systemd_cannot_be_asked():
    def runner(*a, **kw):
        raise FileNotFoundError("systemctl")

    assert poll.timer_state(runner=runner) == "unknown"
```

Add to `tests/test_doctor.py`, following that file's fixture style:

```python
def test_doctor_fails_when_the_poll_timer_is_not_running(monkeypatch, tmp_path):
    """A dead poller looks exactly like the staleness the feature removes, so it
    has to be visible where drift is already policed."""
    reg = env_with_accounts(monkeypatch, tmp_path)     # existing helper
    monkeypatch.setattr(doctor.poll, "timer_state", lambda: "inactive")
    check = _named(doctor.run(reg), "usage: poll timer")
    assert check.ok is False
    assert "systemctl --user enable --now ccas-poll.timer" in check.detail


def test_doctor_passes_when_the_poll_timer_is_active(monkeypatch, tmp_path):
    reg = env_with_accounts(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor.poll, "timer_state", lambda: "active")
    assert _named(doctor.run(reg), "usage: poll timer").ok is True


def test_doctor_reports_each_accounts_reading_age_and_token_horizon(monkeypatch, tmp_path):
    """Informational, not a failure: an idle account past its token horizon is
    expected, and the user needs to be able to see that is what happened."""
    reg = env_with_accounts(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor.poll, "timer_state", lambda: "active")
    slug = reg["accounts"][0]["slug"]
    (paths.account_dir(slug) / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "t",
                           "expiresAt": int((time.time() + 7200) * 1000)}}))
    checks = doctor.run(reg)
    row = _named(checks, f"{slug}: usage freshness")
    assert row.ok is True
    assert "token" in row.detail
```

`_named(checks, label)` is a one-line helper — add it to `test_doctor.py` if it
is not already there:

```python
def _named(checks, label):
    return next(c for c in checks if c.label == label)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_doctor.py tests/test_poll.py -k "timer or freshness" -v`
Expected: FAIL — no `timer_state`, and `StopIteration` from `_named`.

- [ ] **Step 3: Implement**

In `ccas/poll.py`, add `import subprocess` and:

```python
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
```

In `ccas/doctor.py`, import `poll` and `usage`, then add:

```python
def _poll_timer() -> Check:
    """Is the five-minute poll actually running?

    Without it an idle account's numbers freeze at its last session, which is
    indistinguishable from the feature not existing — so a dead timer has to be
    reported here rather than discovered by mistrusting the bar.
    """
    state = poll.timer_state()
    return Check(state == "active", "usage: poll timer",
                 "" if state == "active" else
                 f"systemctl says {state}; start it with: "
                 "systemctl --user enable --now ccas-poll.timer")


def _usage_freshness(account: dict) -> Check:
    """How old this account's reading is, and how long its token can still be
    polled with. Always passes: both facts are expected states, not faults. An
    account past its token horizon is quiet by design — CCAS never renews."""
    name = label.display_name(account)
    now = time.time()
    reading = usage.load(account["slug"])
    age = (f"recorded {poll.age(now - reading['fetched_at'])} ago"
           if reading else "nothing recorded yet")
    credentials = poll.access_token(account["slug"])
    if credentials is None:
        token = "no readable token"
    elif credentials[1] <= now:
        token = f"token expired {poll.age(now - credentials[1])} ago — polling is quiet"
    else:
        token = f"token good for {poll.age(credentials[1] - now)}"
    return Check(True, f"{account['slug']}: usage freshness", f"{name}: {age}; {token}")
```

Add `import time` to `doctor.py` if it is not already imported. Wire both into
`run()`:

```python
    checks = [_claude_home_has_no_symlinks(), *_binaries(),
              _no_legacy_shell_function(), _statusline_hook(), _poll_timer(),
              _panel_dependencies(),
              *_registry_checks(reg),
              *_waybar_matches(reg)]
    for account in reg["accounts"]:
        checks.extend(_account_checks(account))
        checks.append(_usage_freshness(account))
```

`poll.age` is already public (Task 2), because `doctor.py` reads it across the
module boundary — nothing to rename here.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest`
Expected: PASS. The doctor tests that assert on the full check list may need the
new rows accounted for — if one breaks on a count, fix the count, not the rows.

- [ ] **Step 5: Commit**

```bash
git add ccas/doctor.py ccas/poll.py tests/test_doctor.py tests/test_poll.py
git commit -m "doctor: is the timer alive, and how fresh is each account

A poller that quietly died looks exactly like the staleness it exists to remove.
The per-account row is informational — an idle account past its token horizon is
expected, and CCAS never renews a token to avoid it."
```

---

### Task 7: prove it on the real system, then write it down

No new code. This is the task that decides whether the feature works, because
every test above stubbed the network on purpose.

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-07-27-usage-poll-design.md`
- Possibly modify: `docs/why.md` (only if something below surprises you)

- [ ] **Step 1: Install and run it by hand**

```bash
cd ~/ccas && ./install.sh
ccs poll --force
```

Expected: one line per account, both `ok` with a `5h …` detail. `ccs` runs the
**installed** copy — if the output looks like the old code, the install did not
take.

- [ ] **Step 2: Check the invariant around your own command**

```bash
before=$(stat -c %Y ~/.claude/.credentials.json)
for slug in vsed vo-se-15th; do
  stat -c "%Y $slug" ~/.cc-accounts/$slug/.credentials.json
done
ccs poll --force
[ "$before" = "$(stat -c %Y ~/.claude/.credentials.json)" ] || echo "BUG: wrote ~/.claude"
for slug in vsed vo-se-15th; do
  stat -c "%Y $slug" ~/.cc-accounts/$slug/.credentials.json
done
find ~/.claude -maxdepth 1 -type l    # must stay empty
```

Expected: every credentials mtime unchanged, no symlinks. If `~/.claude`'s
mtime moved, prove it was not ours before calling it a bug — a default-account
Claude Code session refreshes that token on its own (`CLAUDE.md`).

- [ ] **Step 3: Prove it fixes the thing it was built for**

The point of the feature is the account with no session running. Note the idle
account's reading age, wait for the timer, and see it move without a session:

```bash
ccs usage
systemctl --user list-timers ccas-poll.timer
sleep 330 && ccs usage        # background it or come back; do not block on it
```

Expected: the idle account's "… ago" gets younger without any session having
been started, and its source reads `oauth`. This is the whole feature; do not
report it working on the strength of the unit tests.

- [ ] **Step 4: Check the bar actually repainted**

`grim` plus PIL cropping, per `CLAUDE.md` — Waybar is on `HDMI-A-1`
(x 2560–4480). Compare the widget before and after a poll that changed a number.
Do not ask the user to look at it.

- [ ] **Step 5: Confirm the failure path is quiet**

```bash
systemctl --user stop ccas-poll.timer
ccs doctor            # must report the timer as inactive, rc 1
systemctl --user start ccas-poll.timer
journalctl --user -u ccas-poll -n 20 --no-pager
```

- [ ] **Step 6: Write it down**

- `CLAUDE.md`: add `poll.py` to the module table ("the session-free usage
  fetch: the credential read, the freshness gate, the request"), add `ccs poll`
  to the commands block, and add a paragraph to **Usage limits** saying the hook
  serves the active account, the timer serves the idle one, CCAS never refreshes
  a token, and the horizon that buys is about eight hours.
- `README.md`: one line for `ccs poll` wherever the commands are listed.
- The spec: change the status line to `**shipped 2026-07-27**` (or the real
  date).
- `docs/why.md`: **only** if something on the real system contradicted the plan.
  Not for routine changes and not for status.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Document the poll, and mark the spec shipped

Verified on the real system: the idle account's reading moved with no session
running, no credentials file was written, and ~/.claude was untouched."
```

---

## Self-review notes

Checked against the spec, section by section:

- **Why / the three symptoms** — no code, covered by the module docstring in
  Task 2 and the `CLAUDE.md` paragraph in Task 7.
- **Decision, route C** — Tasks 2 and 3.
- **`ccas/poll.py` table** — every function appears: `access_token` and `due`
  (Task 2), `fetch` (Task 3), `poll`/`poll_account` (Task 2).
- **`usage.py` third shape and the `record` split** — Task 1.
- **Never refresh** — Task 2, pinned by
  `test_polling_never_writes_the_credentials_file` and
  `test_an_expired_token_is_not_fetched_with_and_not_renewed`.
- **Cadence and skipping fresh accounts** — Task 2 (`due`), Task 5 (the timer).
- **The timer, trashing an existing unit** — Task 5.
- **CLI and audit** — Tasks 4 and 6.
- **What does not change** — no task adds a bar state, a format token, or a
  per-account setting; Task 4's exit-code test pins the "no crying wolf" half.
- **Testing** — the injected fetcher is a parameter from Task 2 onward, and
  `test_polling_never_touches_claude_home` is the canary the spec asks for.

One naming decision made while writing this: `poll.age` is public from Task 2
rather than private, because `doctor.py`'s freshness row reads it in Task 6.
