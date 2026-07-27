import ccas.format as fmt
import ccas.label as label

# The glyph render() prefixes every label with, in the fixture's own colour.
ICON = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


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
    assert label.render(account(nickname=None, format="%name"), 1) == ICON
    assert label.render(account(nickname="", format="%name"), 1) == ICON


def test_the_email_fallback_survives_where_identity_matters():
    """display_name still names the account for the menu title row and the
    account chooser, where two blank rows would be unpickable."""
    assert label.display_name(account(nickname=None)) == "w@example.com"


def test_render_delegates_every_account_to_the_format():
    """One mode means one code path: the label is whatever the format string
    says, with the widget's own glyph in front of it."""
    a = account(format="%name", color="#89b4fa")
    assert label.render(a, 1) == fmt.render(a, 1)


def test_hide_icon_removes_the_glyph_and_leaves_the_text():
    """No alpha='1' spacer any more. The glyph is not named in the format
    string, so an invisible one would reserve its width in exactly the labels
    that asked not to have one."""
    out = label.render(account(hide_icon=True, format="%name"), 1)
    assert "✻" not in out
    assert out == "<span size='110%' color='#ffffff'>work</span>"


def test_nickname_is_pango_escaped():
    out = label.render(account(nickname="a & b <c>", format="%name"), 1)
    assert "a &amp; b &lt;c&gt;" in out
    assert "<c>" not in out


def test_the_account_colour_reaches_the_glyph():
    """By definition rather than through a token: the ✻ is the widget's own
    mark, so there is no format_colors entry that could disagree with it."""
    assert "#fab387" in label.render(account(color="#fab387"), 1)
    assert "#f5c2e7" in label.render(account(color="#f5c2e7"), 1)


def test_a_hidden_glyph_and_an_empty_format_is_the_invisible_case():
    """The glyph is no longer a token, so the test is the format string being
    empty rather than being nothing but %icon."""
    a = account(hide_icon=True, format="   ")
    assert label.check_invisible_warning(a) is True
    assert label.render(a, 1) == ""


def test_a_hidden_glyph_with_text_left_is_not_invisible():
    a = account(hide_icon=True, format="%name", nickname="n")
    assert label.check_invisible_warning(a) is False


def test_a_retired_icon_token_is_visible_text_and_warns_about_nothing():
    """The warning used to strip `%icon` out of the format before asking, back
    when it named the glyph. It names nothing now — it renders as its own four
    characters, which is a label the user can see and read."""
    a = account(hide_icon=True, format="%icon")
    assert label.render(a, 1) == "%icon"
    assert label.check_invisible_warning(a) is False


def test_warning_fires_once_on_entering_the_combination():
    a = account(hide_icon=True, format="   ")
    assert label.check_invisible_warning(a) is True
    assert a["warned_invisible"] is True
    assert label.check_invisible_warning(a) is False


def test_warning_rearms_after_leaving_the_combination():
    a = account(hide_icon=True, format="   ")
    assert label.check_invisible_warning(a) is True
    a["format"] = "%name"
    assert label.check_invisible_warning(a) is False
    assert a["warned_invisible"] is False
    a["format"] = "   "
    assert label.check_invisible_warning(a) is True


def test_warning_never_fires_outside_the_combination():
    """A hidden glyph beside anything else is still a visible module, and an
    empty format with the glyph shown is the "icon only" case, not a fault."""
    assert label.check_invisible_warning(
        account(hide_icon=True, format="%name")) is False
    assert label.check_invisible_warning(account(format="   ")) is False


def test_icon_is_larger_than_the_bar_text():
    """The glyph is the thing you aim at; the nickname beside it stays at the
    bar's font size."""
    out = label.render(account(format="%name"), 1)
    assert out.startswith(f"<span size='{label.ICON_SIZE}'")
    assert "work" in out.split("</span>")[1]


def test_the_state_marks_are_the_vetted_codepoints():
    """U+25CF/U+25CB, not ☑/☐. The bar's font stack starts with FontAwesome,
    which covers U+2611 but not U+2610, so a checkbox pair came from two fonts
    at two sizes and the checked state read as empty. Changing these means
    re-checking the new glyph against that exact stack with pango-view."""
    assert label.MARK_ON == "●"
    assert label.MARK_OFF == "○"
