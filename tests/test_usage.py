import importlib
import json

import ccas.paths as paths
import ccas.usage as usage


def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_NO_RELOAD", "1")
    importlib.reload(paths)
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / "accts" / "work").mkdir(parents=True, exist_ok=True)
    return tmp_path


def statusline(five=(94.0, 1785000600), seven=(19.0, 1785229200)):
    limits = {}
    if five:
        limits["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if seven:
        limits["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    return {"rate_limits": limits}


# ── the two input shapes ──────────────────────────────────────────────────────

def test_both_shapes_normalise_to_the_same_reading():
    """The statusline gives 0-100 under used_percentage and epoch seconds; the
    .claude.json cache gives 0-100 under utilization and an ISO 8601 string with
    a Z and fractional seconds. One fact, two shapes; only this module sees both.
    """
    hook = usage.from_statusline(statusline(), now=1784999999.0)
    cache = usage.from_cache({"cachedUsageUtilization": {
        "fetchedAtMs": 1784999999000,
        "utilization": {
            "five_hour": {"utilization": 94, "resets_at": "2026-07-25T17:30:00.266410Z"},
            "seven_day": {"utilization": 19, "resets_at": "2026-07-28T09:00:00Z"}}}})
    assert hook["five_hour"] == {"percent": 94.0, "resets_at": 1785000600}
    assert cache["five_hour"] == {"percent": 94.0, "resets_at": 1785000600}
    assert hook["fetched_at"] == cache["fetched_at"] == 1784999999.0
    assert hook["source"] == "statusline" and cache["source"] == "cache"


def test_a_window_may_be_independently_absent():
    """The docs are explicit that five_hour and seven_day each come and go."""
    reading = usage.from_statusline(statusline(seven=None), now=1.0)
    assert reading["five_hour"] is not None
    assert reading["seven_day"] is None


def test_a_payload_without_rate_limits_is_not_a_reading():
    """Before the first API response, and for non-subscribers, the key is simply
    absent — which must not be mistaken for "no quota used"."""
    assert usage.from_statusline({}, now=1.0) is None
    assert usage.from_statusline({"rate_limits": {}}, now=1.0) is None
    assert usage.from_cache({}) is None


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

    The measured body's `…:59.757453` is the minute above it, not the second
    below: the anchor is minute-aligned and the fraction is jitter — see
    `test_the_endpoints_sub_second_jitter_lands_on_one_anchor`.
    """
    reading = usage.from_oauth(oauth(), now=1785104289.0)
    assert reading["five_hour"] == {"percent": 3.0, "resets_at": 1785120000}
    assert reading["seven_day"]["percent"] == 0.0
    assert reading["source"] == "oauth"
    assert reading["fetched_at"] == 1785104289.0


def test_the_endpoints_sub_second_jitter_lands_on_one_anchor():
    """Three responses for *one* window, measured 2026-07-28 twenty seconds
    apart: the endpoint's `resets_at` wobbles either side of the minute it means.
    Truncating the fraction quantised them to two different seconds — and so to
    two different minutes — and `%5hreset` on the bar flipped 18:09 ↔ 18:10 on
    every poll, each flip a change `record_reading` had to write and signal.

    The anchor is minute-aligned at the source: the statusline hands the same
    window a whole epoch on the minute, which is what these must agree with.
    """
    jitter = ("2026-07-28T16:09:59.820816+00:00",
              "2026-07-28T16:10:00.488736+00:00",
              "2026-07-28T16:09:59.956668+00:00")
    epochs = {usage.from_oauth(oauth(five=(6.0, iso)), now=1.0)["five_hour"]
              ["resets_at"] for iso in jitter}
    assert epochs == {1785255000}
    assert 1785255000 % 60 == 0
    assert usage.from_statusline(statusline(five=(6.0, 1785255000)),
                                 now=1.0)["five_hour"]["resets_at"] == 1785255000


def test_jitter_alone_is_not_a_change(monkeypatch, tmp_path):
    """The half of the flip that cost a write. Same percentage, same minute, a
    different fraction of a second: nothing moved, so nothing is written and the
    bar is not signalled."""
    env(monkeypatch, tmp_path)
    first = usage.from_oauth(
        oauth(five=(6.0, "2026-07-28T16:09:59.820816+00:00")), now=100.0)
    assert usage.record_reading("work", first) is True
    again = usage.from_oauth(
        oauth(five=(6.0, "2026-07-28T16:10:00.488736+00:00")), now=200.0)
    assert usage.record_reading("work", again) is False


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


# ── classification ────────────────────────────────────────────────────────────

def test_a_future_reset_bounds_the_percentage():
    reading = usage.from_statusline(statusline(five=(94.0, 2000)), now=1000.0)
    state = usage.state(reading, "five_hour", now=1500.0)
    assert state.kind == usage.BOUNDED
    assert state.percent == 94.0


def test_a_past_reset_means_the_window_rolled_over():
    """No age cutoff anywhere: a 5-hour reading older than five hours must have
    a reset in the past, so staleness and rollover are the same test."""
    reading = usage.from_statusline(statusline(five=(94.0, 2000)), now=1000.0)
    assert usage.state(reading, "five_hour", now=999999.0).kind == usage.IDLE
    assert usage.state(None, "five_hour", now=1.0).kind == usage.ABSENT


def test_a_reading_that_names_no_window_is_idle_not_absent():
    """The bug: an account left alone long enough stops being reported a 5-hour
    window at all, and the label built from 5h tokens vanished. A reading that
    says the window is not running is information — the quota refilled — where
    no reading at all is ignorance."""
    reading = usage.from_statusline(statusline(five=None, seven=(11.0, 900000)),
                                    now=1000.0)
    assert usage.state(reading, "five_hour", now=1000.0).kind == usage.IDLE
    assert usage.state(None, "five_hour", now=1000.0).kind == usage.ABSENT


def test_an_idle_window_reads_as_nothing_used():
    """0.0, not None: the percentage is the fact — a rolled-over window has had
    nothing spent in it — and it is what keeps %5hused rendering. The dead
    pre-reset number is not carried across."""
    reading = usage.from_statusline(statusline(five=(94.0, 2000)), now=1000.0)
    st = usage.state(reading, "five_hour", now=999999.0)
    assert st.percent == 0.0
    assert st.resets_at is None
    assert usage.state(None, "five_hour", now=1.0).percent is None


def test_open_is_gone():
    """Folded into IDLE rather than kept beside it: a payload that omits the
    window and a reset that has passed are one fact, and this repo retires a
    value instead of growing a read path that understands both."""
    assert not hasattr(usage, "OPEN")


# ── recording ─────────────────────────────────────────────────────────────────

def test_record_writes_the_reading_into_the_account_directory(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert usage.record("work", statusline(), now=10.0) is True
    written = json.loads((paths.account_dir("work") / paths.USAGE_FILE).read_text())
    assert written["five_hour"]["percent"] == 94.0
    assert written["fetched_at"] == 10.0


def test_an_unchanged_reading_is_not_rewritten(monkeypatch, tmp_path):
    """The statusline fires every few hundred ms and utilisation moves every few
    minutes. Writing every time would be thousands of writes an hour — and the
    signal that rides on a write would be thousands of pkills."""
    env(monkeypatch, tmp_path)
    usage.record("work", statusline(), now=10.0)
    path = paths.account_dir("work") / paths.USAGE_FILE
    before = path.stat().st_mtime_ns
    assert usage.record("work", statusline(), now=99.0) is False
    assert path.stat().st_mtime_ns == before
    # fetched_at stays the moment the reading was first seen, not the last tick.
    assert json.loads(path.read_text())["fetched_at"] == 10.0


def test_an_empty_payload_never_clobbers_a_good_reading(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    usage.record("work", statusline(), now=10.0)
    assert usage.record("work", {}, now=20.0) is False
    written = json.loads((paths.account_dir("work") / paths.USAGE_FILE).read_text())
    assert written["five_hour"]["percent"] == 94.0


# ── which account, and the invariant ──────────────────────────────────────────

def test_the_default_account_is_not_an_account(monkeypatch, tmp_path):
    """CLAUDE_CONFIG_DIR unset, or pointing at ~/.claude, is the default account.
    settings.json is shared, so the hook fires there too — and recording there
    would mean writing inside ~/.claude, which nothing in CCAS may ever do."""
    env(monkeypatch, tmp_path)
    assert usage.account_slug({}) is None
    assert usage.account_slug({"CLAUDE_CONFIG_DIR": str(paths.claude_home())}) is None
    assert usage.account_slug(
        {"CLAUDE_CONFIG_DIR": str(paths.claude_home() / "projects")}) is None


def test_an_account_directory_resolves_to_its_slug(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert usage.account_slug(
        {"CLAUDE_CONFIG_DIR": str(paths.account_dir("work"))}) == "work"
    assert usage.account_slug(
        {"CLAUDE_CONFIG_DIR": str(paths.account_dir("work")) + "/"}) == "work"
    assert usage.account_slug({"CLAUDE_CONFIG_DIR": str(tmp_path)}) is None


# ── reading back ──────────────────────────────────────────────────────────────

def test_read_prefers_whichever_source_is_fresher(monkeypatch, tmp_path):
    """A `/usage` run leaves a cache behind for an account whose hook never
    fired; a wired hook must not then be dragged backwards by that old cache."""
    env(monkeypatch, tmp_path)
    directory = paths.account_dir("work")
    (directory / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {
        "fetchedAtMs": 5000, "utilization": {
            "five_hour": {"utilization": 11, "resets_at": "2026-07-25T17:30:00Z"}}}}))

    assert usage.read("work")["five_hour"]["percent"] == 11.0   # cache alone

    usage.record("work", statusline(five=(94.0, 1785000600)), now=1.0)
    assert usage.read("work")["five_hour"]["percent"] == 11.0   # cache is fresher

    usage.record("work", statusline(five=(96.0, 1785000600)), now=6000.0)
    assert usage.read("work")["five_hour"]["percent"] == 96.0   # hook is fresher


def test_read_of_an_account_with_nothing_recorded(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert usage.read("work") is None


# ── formatting ────────────────────────────────────────────────────────────────

def test_the_bar_shows_the_clock_coloured_by_pressure():
    reading = usage.from_statusline(statusline(five=(94.0, 2000), seven=None), now=0)
    text, attrs = usage.bar(reading, now=1000.0)
    assert text == usage.reset_clock(2000)
    assert attrs == f"color='{usage.PEACH}'"

    quiet = usage.from_statusline(statusline(five=(12.0, 2000), seven=None), now=0)
    assert usage.bar(quiet, now=1000.0)[1] == f"alpha='{usage.DIM}'"


def test_an_open_or_absent_window_puts_nothing_on_the_bar():
    """No pressure and no data both mean nothing to warn about. The difference
    between them is said in words, in the picker and in `ccs doctor`."""
    rolled = usage.from_statusline(statusline(five=(94.0, 2000), seven=None), now=0)
    assert usage.bar(rolled, now=999999.0) is None
    assert usage.bar(None, now=1000.0) is None


def test_the_seven_day_window_takes_over_only_when_it_binds():
    """Its reset is days away, so the clock is not the actionable fact there."""
    both = usage.from_statusline(statusline(five=(40.0, 2000), seven=(94.0, 900000)), now=0)
    assert usage.bar(both, now=1000.0) == ("7d 94%", f"color='{usage.RED}'")

    below = usage.from_statusline(statusline(five=(40.0, 2000), seven=(82.0, 900000)), now=0)
    assert usage.bar(below, now=1000.0)[0] == usage.reset_clock(2000)

    outranked = usage.from_statusline(statusline(five=(97.0, 2000), seven=(94.0, 900000)), now=0)
    assert usage.bar(outranked, now=1000.0)[0] == usage.reset_clock(2000)


def test_the_lines_name_the_state_in_words():
    bounded = usage.from_statusline(statusline(five=(94.0, 2000), seven=None), now=0)
    assert usage.lines(bounded, now=1000.0) == [
        f"5h ≥94% · clears {usage.reset_time(2000, 1000.0)}"]
    assert usage.lines(bounded, now=999999.0) == ["5h 0% used"]
    assert usage.lines(None, now=1000.0) == [usage.NOT_WIRED]


def test_a_binding_seven_day_window_earns_its_own_line():
    reading = usage.from_statusline(statusline(five=(94.0, 2000), seven=(91.0, 900000)), now=0)
    assert usage.lines(reading, now=1000.0)[1] == \
        f"7d ≥91% · clears {usage.reset_time(900000, 1000.0)}"

    quiet = usage.from_statusline(statusline(five=(94.0, 2000), seven=(19.0, 900000)), now=0)
    assert len(usage.lines(quiet, now=1000.0)) == 1


def test_reset_time_names_the_day_only_when_it_is_another_one():
    """A 5-hour window can cross midnight and a 7-day one always lands on
    another day, so a bare %H:%M would be ambiguous exactly when it matters."""
    import time as _time
    noon = _time.mktime((2026, 7, 25, 12, 0, 0, 0, 0, -1))
    assert usage.reset_time(noon + 3600, noon) == "13:00"
    assert usage.reset_time(noon + 86400, noon) == _time.strftime(
        "%a %H:%M", _time.localtime(noon + 86400))


def test_the_column_says_an_idle_window_and_an_unknown_one_differently():
    """`ccs usage` shows both windows however quiet either is, so it is the one
    place the two must not read alike: 0% used is a measurement, — is not."""
    idle = usage.from_statusline(statusline(five=None, seven=(11.0, 900000)),
                                 now=1000.0)
    assert usage.column(idle, "five_hour", 1000.0) == "5h 0% used"
    assert usage.column(None, "five_hour", 1000.0) == "5h —"


def test_an_idle_window_still_has_nothing_to_warn_about():
    """bar() exists to warn, so IDLE keeps answering None — %5h and %7d stay
    blank rather than announcing that everything is fine."""
    idle = usage.from_statusline(statusline(five=None, seven=(11.0, 900000)),
                                 now=1000.0)
    assert usage.bar(idle, now=1000.0) is None


def test_a_payload_naming_one_window_does_not_erase_the_other(monkeypatch, tmp_path):
    """The rule "a payload with no windows must not erase a good one", one level
    finer — it guarded the both-missing case and let the half-missing one
    through. Found on the real system 2026-07-28: an account with a live 5-hour
    window read `100%` out of nowhere on the bar. A statusline tick had named
    seven_day and no usable five_hour, which wrote `five_hour: null` over a
    running window; `state()` reads that as IDLE, IDLE carries 0.0, and a label
    built from `%5hquotaleft` renders 100 - 0. Nothing was wrong with the
    account and nothing was stale — the reading had been overwritten with less
    than it had.

    Carrying the old window forward cannot lie about a rollover: `state()` calls
    a window whose `resets_at` has passed IDLE from the timestamp alone, so a
    window that really did roll over still reports idle on its own.
    """
    env(monkeypatch, tmp_path)
    live = {"fetched_at": 100.0, "source": "oauth",
            "five_hour": {"percent": 1.0, "resets_at": 12000},
            "seven_day": {"percent": 2.0, "resets_at": 500000}}
    assert usage.record_reading("work", live) is True

    half = usage.from_statusline(
        {"rate_limits": {"seven_day": {"used_percentage": 3, "resets_at": 500000}}},
        now=200.0)
    assert half["five_hour"] is None          # the payload really did name one
    usage.record_reading("work", half)

    stored = usage.load("work")
    assert stored["five_hour"] == {"percent": 1.0, "resets_at": 12000}
    assert stored["seven_day"]["percent"] == 3.0     # the named one still lands
    assert usage.state(stored, "five_hour", 150.0).kind == usage.BOUNDED


def test_a_window_that_rolled_over_is_still_idle_when_it_is_carried(
        monkeypatch, tmp_path):
    """The carry above must not resurrect a dead window: `resets_at` is an
    absolute anchor, so a carried five_hour whose reset has passed reads IDLE
    from the timestamp, exactly as it would have without the carry."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", {
        "fetched_at": 100.0, "source": "oauth",
        "five_hour": {"percent": 40.0, "resets_at": 500},
        "seven_day": {"percent": 2.0, "resets_at": 500000}})
    usage.record_reading("work", usage.from_statusline(
        {"rate_limits": {"seven_day": {"used_percentage": 3, "resets_at": 500000}}},
        now=600.0))
    assert usage.state(usage.load("work"), "five_hour", 600.0).kind == usage.IDLE
