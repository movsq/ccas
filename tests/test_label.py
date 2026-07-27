import ccas.format as fmt
import ccas.label as label


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": "#f38ba8", "hide_icon": False,
        "format": fmt.DEFAULT_FORMAT, "format_colors": {},
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
    icon = "<span size='150%' rise='-800' color='#ffffff'>✻</span>"
    assert label.render(account(nickname=None, format="%icon %name"), 1) == icon
    assert label.render(account(nickname="", format="%icon %name"), 1) == icon


def test_the_email_fallback_survives_where_identity_matters():
    """display_name still names the account for the menu title row and the
    account chooser, where two blank rows would be unpickable."""
    assert label.display_name(account(nickname=None)) == "w@example.com"


def test_render_delegates_every_account_to_the_format():
    """One mode means one code path: the label is whatever the format string
    says, and 'icon only' is now the string '%icon' rather than a mode."""
    a = account(format="%icon", color="#89b4fa")
    assert label.render(a, 1) == fmt.render(a, 1)


def test_hide_icon_uses_alpha_not_removal():
    out = label.render(account(hide_icon=True, format="%icon %name"), 1)
    assert "alpha='1'" in out
    assert "✻" in out, "glyph must stay so the module keeps its width"
    assert out.endswith("<span size='110%' color='#ffffff'>work</span>")


def test_fully_invisible_combination_still_emits_a_glyph():
    out = label.render(account(hide_icon=True, format="%icon"), 1)
    assert out == "<span size='150%' rise='-800' alpha='1'>✻</span>"
    assert out.strip() != "", "an empty label would collapse the module and break clicking"


def test_nickname_is_pango_escaped():
    out = label.render(account(nickname="a & b <c>", format="%icon %name"), 1)
    assert "a &amp; b &lt;c&gt;" in out
    assert "<c>" not in out


def test_the_account_colour_reaches_the_glyph():
    """Through %icon's `account`, which is what a new account is written with —
    the glyph is a token like any other now, not the one the widget's colour
    always leaked into."""
    colors = dict(fmt.DEFAULT_FORMAT_COLORS)
    assert "#fab387" in label.render(
        account(color="#fab387", format_colors=colors), 1)
    assert "#f5c2e7" in label.render(
        account(color="#f5c2e7", format_colors=colors), 1)


def test_warning_fires_once_on_entering_the_combination():
    a = account(hide_icon=True, format="%icon")
    assert label.check_invisible_warning(a) is True
    assert a["warned_invisible"] is True
    assert label.check_invisible_warning(a) is False


def test_warning_rearms_after_leaving_the_combination():
    a = account(hide_icon=True, format="%icon")
    assert label.check_invisible_warning(a) is True
    a["format"] = "%icon %name"
    assert label.check_invisible_warning(a) is False
    assert a["warned_invisible"] is False
    a["format"] = "%icon"
    assert label.check_invisible_warning(a) is True


def test_warning_never_fires_outside_the_combination():
    """A hidden glyph beside anything else is still a visible module, and a
    format of just %icon with the glyph shown is the ordinary case."""
    assert label.check_invisible_warning(
        account(hide_icon=True, format="%icon %name")) is False
    assert label.check_invisible_warning(account(format="%icon")) is False


def test_icon_is_larger_than_the_bar_text():
    """The glyph is the thing you aim at; the nickname beside it stays at the
    bar's font size."""
    out = label.render(account(format="%icon %name"), 1)
    assert out.startswith(f"<span size='{label.ICON_SIZE}'")
    assert "work" in out.split("</span>")[1]


def test_the_state_marks_are_the_vetted_codepoints():
    """U+25CF/U+25CB, not ☑/☐. The bar's font stack starts with FontAwesome,
    which covers U+2611 but not U+2610, so a checkbox pair came from two fonts
    at two sizes and the checked state read as empty. Changing these means
    re-checking the new glyph against that exact stack with pango-view."""
    assert label.MARK_ON == "●"
    assert label.MARK_OFF == "○"
