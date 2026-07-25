import ccas.label as label
import ccas.usage as usage


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": 1, "display": "nickname", "hide_icon": False,
        "warned_invisible": False, "signal": 1,
    }
    base.update(kw)
    return base


def test_display_name_prefers_nickname_then_email():
    assert label.display_name(account()) == "work"
    assert label.display_name(account(nickname=None)) == "w@example.com"
    assert label.display_name(account(nickname="")) == "w@example.com"


def test_cleared_nickname_shows_nothing_on_the_bar():
    """Falling back to the email put a long address in the bar. A cleared
    nickname means "show no text", not "show something else"."""
    assert label.render(account(nickname=None), 1) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"
    assert label.render(account(nickname=""), 1) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


def test_the_email_fallback_survives_where_identity_matters():
    """display_name still names the account for the menu title row and the
    account chooser, where two blank rows would be unpickable."""
    assert label.display_name(account(nickname=None)) == "w@example.com"


def test_render_each_display_mode():
    assert label.render(account(), 1) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>work</span>"
    assert label.render(account(display="index"), 3) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>3</span>"
    assert label.render(account(display="claude code"), 1) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>claude code</span>"
    assert label.render(account(display="icon only"), 1) == "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


def test_hide_icon_uses_alpha_not_removal():
    out = label.render(account(hide_icon=True), 1)
    assert "alpha='1'" in out
    assert "✻" in out, "glyph must stay so the module keeps its width"
    assert out.endswith("<span size='110%'>work</span>")


def test_fully_invisible_combination_still_emits_a_glyph():
    out = label.render(account(hide_icon=True, display="icon only"), 1)
    assert out == "<span size='150%' rise='-800' alpha='1'>✻</span>"
    assert out.strip() != "", "an empty label would collapse the module and break clicking"


def test_nickname_is_pango_escaped():
    out = label.render(account(nickname="a & b <c>"), 1)
    assert "a &amp; b &lt;c&gt;" in out
    assert "<c>" not in out


def test_colour_index_selects_palette_entry():
    assert "#fab387" in label.render(account(color=0), 1)
    assert "#f5c2e7" in label.render(account(color=7), 1)


def test_warning_fires_once_on_entering_the_combination():
    a = account(hide_icon=True, display="icon only")
    assert label.check_invisible_warning(a) is True
    assert a["warned_invisible"] is True
    assert label.check_invisible_warning(a) is False


def test_warning_rearms_after_leaving_the_combination():
    a = account(hide_icon=True, display="icon only")
    assert label.check_invisible_warning(a) is True
    a["display"] = "nickname"
    assert label.check_invisible_warning(a) is False
    assert a["warned_invisible"] is False
    a["display"] = "icon only"
    assert label.check_invisible_warning(a) is True


def test_warning_never_fires_outside_the_combination():
    assert label.check_invisible_warning(account(hide_icon=True)) is False
    assert label.check_invisible_warning(account(display="icon only")) is False


def test_icon_is_larger_than_the_bar_text():
    """The glyph is the thing you aim at; the nickname beside it stays at the
    bar's font size."""
    out = label.render(account(), 1)
    assert out.startswith(f"<span size='{label.ICON_SIZE}'")
    assert "work" in out.split("</span>")[1]


def test_the_state_marks_are_the_vetted_codepoints():
    """U+25CF/U+25CB, not ☑/☐. The bar's font stack starts with FontAwesome,
    which covers U+2611 but not U+2610, so a checkbox pair came from two fonts
    at two sizes and the checked state read as empty. Changing these means
    re-checking the new glyph against that exact stack with pango-view."""
    assert label.MARK_ON == "●"
    assert label.MARK_OFF == "○"


# ── the usage token ───────────────────────────────────────────────────────────

def reading(percent=94.0, resets_at=2000):
    return {"fetched_at": 0.0, "source": "statusline",
            "five_hour": {"percent": percent, "resets_at": resets_at},
            "seven_day": None}


def test_every_display_mode_carries_the_usage_token():
    """CCAS is an account chooser and the question it answers is a quota
    question, so usage is the bar's subject — including in `icon only`."""
    for mode in ("nickname", "index", "claude code", "icon only"):
        rendered = label.render(account(display=mode), 1, reading(), now=1000.0)
        assert usage.reset_clock(2000) in rendered
        assert rendered.startswith("<span size='150%'")


def test_the_token_follows_the_name_rather_than_replacing_it():
    rendered = label.render(account(), 1, reading(), now=1000.0)
    assert rendered.endswith(
        f"<span size='110%'>work</span> "
        f"<span size='110%' color='{usage.PEACH}'>{usage.reset_clock(2000)}</span>")


def test_a_cleared_nickname_still_leaves_room_for_the_token():
    rendered = label.render(account(nickname=""), 1, reading(), now=1000.0)
    assert rendered == (
        "<span size='150%' rise='-800' color='#f38ba8'>✻</span> "
        f"<span size='110%' color='{usage.PEACH}'>{usage.reset_clock(2000)}</span>")


def test_no_reading_renders_the_bar_exactly_as_before():
    """Open and absent are both "nothing to warn about", and the label is a
    warning device. The difference between them is said in words elsewhere."""
    assert label.render(account(), 1, None, now=1000.0) == label.render(account(), 1)
    rolled_over = label.render(account(), 1, reading(), now=999999.0)
    assert rolled_over == label.render(account(), 1)
