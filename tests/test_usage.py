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
          seven=(0.0, "2026-08-02T18:59:59.757477+00:00"), fable=None):
    """The /api/oauth/usage body, as measured on 2026-07-27.

    `fable` is `(percent, resets_at)` for the model-scoped weekly window, which
    the body carries nowhere near the other two: not as a top-level key but as
    an entry in `limits[]`, measured 2026-08-17 and quoted in
    `docs/usage-limits-research.md`.
    """
    body = {}
    if five:
        body["five_hour"] = {"utilization": five[0], "resets_at": five[1],
                             "limit_dollars": None}
    if seven:
        body["seven_day"] = {"utilization": seven[0], "resets_at": seven[1]}
    body["seven_day_opus"] = None          # the real body carries several of these
    body["limits"] = [{"kind": "session", "group": "session", "percent": 3,
                       "severity": "normal", "scope": None, "is_active": True}]
    if fable:
        body["limits"].append(scoped_limit(*fable))
    return body


def scoped_limit(percent, resets_at, name="Fable"):
    """One `limits[]` entry, spelled as the endpoint spells it."""
    return {"kind": "weekly_scoped", "group": "weekly", "percent": percent,
            "severity": "normal", "resets_at": resets_at, "is_active": False,
            "scope": {"model": {"id": None, "display_name": name},
                      "surface": None}}


FABLE_AT = "2026-08-23T19:00:00.350093+00:00"
FABLE_EPOCH = 1787511600      # 2026-08-23 19:00 UTC, the minute it anchors to
FIVE_AT = "2026-08-17T09:00:00Z"
FIVE_EPOCH = 1786957200


# ── the model-scoped window ───────────────────────────────────────────────────

def test_the_fable_window_is_read_out_of_the_scoped_limits():
    """It is not a window beside five_hour and seven_day in the body: the
    endpoint names it only inside `limits[]`, as the weekly entry whose scope
    carries a model. Measured on the real account 2026-08-17."""
    reading = usage.from_oauth(oauth(fable=(5.0, FABLE_AT)), now=1_000.0)
    assert reading[usage.FABLE] == {"percent": 5.0, "resets_at": FABLE_EPOCH}


def test_an_account_that_is_not_told_about_fable_has_no_fable_window():
    """The measured difference between the two accounts on 2026-08-17: one
    body's `limits[]` carries a weekly_scoped entry and the other's does not.

    ABSENT, not IDLE — and this is the one place the two plan windows' rule is
    wrong. Silence about five_hour says the quota refilled, because every
    subscription has a 5-hour window. Silence about a model-scoped one says the
    account has no such window at all, and "fable 0% used" would be a meter for
    a limit that does not exist.
    """
    reading = usage.from_oauth(oauth(), now=1_000.0)
    assert reading[usage.FABLE] is None
    assert usage.state(reading, usage.FABLE, now=1_000.0).kind == usage.ABSENT
    assert usage.state(reading, "five_hour", now=1_000.0).kind == usage.BOUNDED


def test_the_statusline_names_the_fable_window_in_a_third_spelling():
    """`rate_limits.model_scoped`, added by the bundle from the same `limits[]`
    it filters through its own allowlist. Two traps in one payload: the
    percentage is `utilization` where its siblings use `used_percentage`, and
    the reset is an **ISO string** where five_hour's is epoch seconds — the
    bundle converts it on the way out.
    """
    payload = statusline()
    payload["rate_limits"]["model_scoped"] = [
        {"display_name": "Fable", "utilization": 5, "resets_at": FABLE_AT}]
    reading = usage.from_statusline(payload, now=1_000.0)
    assert reading[usage.FABLE] == {"percent": 5.0, "resets_at": FABLE_EPOCH}
    assert usage.from_statusline(statusline(), now=1_000.0)[usage.FABLE] is None


def test_the_bucket_is_matched_however_the_server_spells_the_model():
    """Claude Code's own allowlist for this machine reads ["Fable", "Fable 5"]
    (`tengu_usage_overage_included_models`, 2026-08-17), so the display name is
    the model's marketing name and it carries a version. The prefix is the
    stable half of it."""
    for name in ("Fable", "Fable 5", "fable"):
        body = {"limits": [scoped_limit(7.0, FABLE_AT, name=name)]}
        assert usage.from_oauth(body, now=1.0)[usage.FABLE]["percent"] == 7.0
    other = {"limits": [scoped_limit(7.0, FABLE_AT, name="Opus")]}
    assert usage.from_oauth(other, now=1.0) is None


def test_a_fable_window_the_account_has_but_is_not_using_is_idle():
    """The endpoint answers an unused window as a zero with no reset — measured
    on the 5-hour window of an idle account the same day. The percentage alone
    is what says the account *has* the window, so it is recorded without a
    reset rather than dropped, and reads IDLE where a missing entry reads
    ABSENT.
    """
    reading = usage.from_oauth({"limits": [scoped_limit(0.0, None)]}, now=1.0)
    assert reading[usage.FABLE] == {"percent": 0.0, "resets_at": None}
    assert usage.state(reading, usage.FABLE, now=1.0).kind == usage.IDLE


def test_a_rolled_over_fable_window_is_idle_from_its_timestamp():
    """The same anchor rule as the other two: past means it refilled."""
    reading = usage.from_oauth(oauth(fable=(90.0, FABLE_AT)), now=1.0)
    assert usage.state(reading, usage.FABLE, FABLE_EPOCH - 60).kind == usage.BOUNDED
    assert usage.state(reading, usage.FABLE, FABLE_EPOCH + 60).kind == usage.IDLE


def test_silence_about_fable_does_not_erase_it_either(monkeypatch, tmp_path):
    """`_carried`, which is why the hook can be quiet about a window the poll
    measured. The allowlist that decides whether the bundle emits `model_scoped`
    is a remote config, so a statusline tick may name the two plan windows and
    nothing else while the endpoint is still reporting a live Fable window.
    """
    env(monkeypatch, tmp_path)
    assert usage.record_reading(
        "work", usage.from_oauth(oauth(fable=(5.0, FABLE_AT)), now=100.0)) is True
    usage.record_reading("work", usage.from_statusline(
        statusline(five=(50.0, 1785000600)), now=200.0))
    assert usage.load("work")[usage.FABLE] == {"percent": 5.0,
                                               "resets_at": FABLE_EPOCH}


def test_a_moved_fable_window_is_a_change_worth_writing(monkeypatch, tmp_path):
    """It is in WINDOWS, so it reaches the write-on-change rule the other two
    obey — and with it the signal that rides on the write."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", usage.from_oauth(oauth(fable=(5.0, FABLE_AT)),
                                                  now=100.0))
    assert usage.record_reading(
        "work", usage.from_oauth(oauth(fable=(5.0, FABLE_AT)), now=200.0)) is False
    assert usage.record_reading(
        "work", usage.from_oauth(oauth(fable=(6.0, FABLE_AT)), now=300.0)) is True


def test_the_fable_window_takes_the_label_when_it_is_the_binding_one():
    """The rule the 7-day window already had, and it was never about that window
    in particular: a weekly reset is days away, so above the threshold the
    number is the actionable fact and the 5-hour clock is not."""
    reading = usage.from_oauth(
        oauth(five=(40.0, FIVE_AT), fable=(94.0, FABLE_AT)),
        now=1.0)
    now = FIVE_EPOCH - 3600     # both windows still running
    assert usage.bar(reading, now) == ("fable 94%", f"color='{usage.RED}'")
    assert usage.lines(reading, now)[1] == \
        f"fable ≥94% · clears {usage.reset_time(FABLE_EPOCH, now)}"


def test_a_quiet_fable_window_stays_off_the_bar_and_out_of_the_lines():
    reading = usage.from_oauth(
        oauth(five=(40.0, FIVE_AT), fable=(12.0, FABLE_AT)),
        now=1.0)
    now = FIVE_EPOCH - 3600
    assert usage.bar(reading, now)[0] == usage.reset_clock(FIVE_EPOCH)
    assert len(usage.lines(reading, now)) == 1


def test_the_column_says_the_fable_window_like_any_other():
    now = FABLE_EPOCH - 86400
    reading = usage.from_oauth(oauth(fable=(5.0, FABLE_AT)), now=1.0)
    assert usage.column(reading, usage.FABLE, now) == \
        f"fable ≥5% clears {usage.reset_time(FABLE_EPOCH, now)}"
    assert usage.column(usage.from_oauth(oauth(), now=1.0), usage.FABLE, now) == \
        "fable —"


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


# ── the stall: a poller that has stopped is not an account at rest ────────────

def stalled(tmp_path, reason=None, since=0.0, failures=1, retry_at=None,
            slug="work"):
    reason = usage.LOCKED_OUT if reason is None else reason
    (tmp_path / "accts" / slug / paths.POLL_FAIL_FILE).write_text(json.dumps({
        "reason": reason, "note": "", "since": since, "at": since,
        "failures": failures, "retry_at": retry_at}))


ROLLED_OVER = {"fetched_at": 100.0, "source": "oauth",
               "five_hour": {"percent": 27.0, "resets_at": 500},
               "seven_day": {"percent": 3.0, "resets_at": 900000}}


def test_a_window_that_rolled_over_while_the_poll_was_stopped_is_not_idle(
        monkeypatch, tmp_path):
    """The whole point. IDLE is a measurement — "this window is not running, so
    nothing has been spent in it" — and it is only worth that much while
    something is still asking. With the poll stopped, the same reading says only
    that the window we last saw has rolled over; whether the account has spent
    the new one is exactly what nobody has looked at.

    Measured 2026-09-02: an account whose token had been rejected since midnight
    read `0% used` on the bar, at full brightness, all morning.
    """
    env(monkeypatch, tmp_path)
    now = 500 + usage.STALL_GRACE + 1
    usage.record_reading("work", ROLLED_OVER)
    assert usage.state(usage.read("work"), "five_hour", now).kind == usage.IDLE
    stalled(tmp_path, since=now - usage.STALL_GRACE - 1)
    assert usage.state(usage.read("work"), "five_hour", now).kind == usage.STALLED


def test_a_stalled_window_carries_no_percentage(monkeypatch, tmp_path):
    """IDLE carries 0.0 because that is the fact. This one carries None for the
    same reason — `%5hused` renders empty rather than a zero nobody measured,
    and a panel bar is not drawn rather than drawn at the bottom."""
    env(monkeypatch, tmp_path)
    now = 500 + usage.STALL_GRACE + 1
    usage.record_reading("work", ROLLED_OVER)
    stalled(tmp_path, since=now - usage.STALL_GRACE - 1)
    st = usage.state(usage.read("work"), "five_hour", now)
    assert st.percent is None and st.resets_at is None


def test_a_stall_leaves_a_bounded_window_alone(monkeypatch, tmp_path):
    """`resets_at` is an absolute anchor, so a reading whose reset is still
    ahead is a lower bound whoever is or is not asking. Nothing to withdraw."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", ROLLED_OVER)
    stalled(tmp_path, since=0.0)
    st = usage.state(usage.read("work"), "seven_day", 500 + usage.STALL_GRACE + 1)
    assert st.kind == usage.BOUNDED and st.percent == 3.0


def test_a_stall_younger_than_the_grace_is_a_blip(monkeypatch, tmp_path):
    """One refused tick is a network hiccup, and the reading it could not
    refresh is five minutes old. The label must not flicker for that."""
    env(monkeypatch, tmp_path)
    now = 500 + usage.STALL_GRACE + 1
    usage.record_reading("work", ROLLED_OVER)
    stalled(tmp_path, since=now - 60)
    assert usage.state(usage.read("work"), "five_hour", now).kind == usage.IDLE


def test_a_stall_does_not_invent_a_reading(monkeypatch, tmp_path):
    """ABSENT is no reading, and a poll that could not produce one has not
    produced one. Doctor's hook and timer checks are still the answer there."""
    env(monkeypatch, tmp_path)
    stalled(tmp_path, since=0.0)
    assert usage.state(usage.read("work"), "five_hour", 99999.0).kind == usage.ABSENT


def test_load_is_the_file_and_read_is_what_to_show(monkeypatch, tmp_path):
    """`due()` and doctor read the reading; the label reads what to say about
    it. Only the second carries the stall, or the poll would be deciding
    whether to ask from a fact about how the answer is displayed."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", ROLLED_OVER)
    stalled(tmp_path, since=0.0)
    assert "stall" not in usage.load("work")
    assert usage.read("work")["stall"]["reason"] == usage.LOCKED_OUT


def test_a_stall_says_which_stop_it_was(monkeypatch, tmp_path):
    """Three stops, three words. "logged out" and "rate limited" ask for
    different things from the user, and neither is fixed by waiting."""
    env(monkeypatch, tmp_path)
    now = 500 + usage.STALL_GRACE + 1
    usage.record_reading("work", ROLLED_OVER)
    for reason, word in ((usage.LOCKED_OUT, "logged out"),
                         (usage.LIMITED, "rate limited"),
                         (usage.UNREACHABLE, "stale")):
        stalled(tmp_path, reason=reason, since=0.0)
        assert word in usage.column(usage.read("work"), "five_hour", now)


def test_the_picker_says_a_stall_where_it_would_have_said_idle(
        monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    now = 500 + usage.STALL_GRACE + 1
    usage.record_reading("work", ROLLED_OVER)
    stalled(tmp_path, since=0.0)
    assert "logged out" in " ".join(usage.lines(usage.read("work"), now))


def test_recording_a_stall_only_writes_when_it_changes(monkeypatch, tmp_path):
    """The rule everything here obeys: a logged-out account is polled every five
    minutes for as long as it stays logged out."""
    env(monkeypatch, tmp_path)
    assert usage.record_stall("work", usage.LOCKED_OUT, 100.0, counts=False)
    assert not usage.record_stall("work", usage.LOCKED_OUT, 400.0, counts=False)
    assert usage.stall("work")["since"] == 100.0
    assert usage.record_stall("work", usage.LIMITED, 700.0, retry_at=1000.0)


def test_clearing_a_stall_is_the_absence_of_one(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    usage.record_stall("work", usage.LOCKED_OUT, 100.0)
    assert usage.clear_stall("work") is True
    assert usage.stall("work") is None
    assert usage.clear_stall("work") is False
