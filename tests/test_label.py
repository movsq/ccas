import ccas.label as label


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
    assert label.render(account(nickname=None), 1) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span>"
    assert label.render(account(nickname=""), 1) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span>"


def test_the_email_fallback_survives_where_identity_matters():
    """display_name still names the account for the menu title row and the
    account chooser, where two blank rows would be unpickable."""
    assert label.display_name(account(nickname=None)) == "w@example.com"


def test_render_each_display_mode():
    assert label.render(account(), 1) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>work</span>"
    assert label.render(account(display="index"), 3) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>3</span>"
    assert label.render(account(display="claude code"), 1) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span> <span size='110%'>claude code</span>"
    assert label.render(account(display="icon only"), 1) == "<span size='x-large' rise='-800' color='#f38ba8'>✻</span>"


def test_hide_icon_uses_alpha_not_removal():
    out = label.render(account(hide_icon=True), 1)
    assert "alpha='1'" in out
    assert "✻" in out, "glyph must stay so the module keeps its width"
    assert out.endswith("<span size='110%'>work</span>")


def test_fully_invisible_combination_still_emits_a_glyph():
    out = label.render(account(hide_icon=True, display="icon only"), 1)
    assert out == "<span size='x-large' rise='-800' alpha='1'>✻</span>"
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
