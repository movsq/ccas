import ccas.format as fmt

ICON = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": 1, "display": "custom", "hide_icon": False,
        "warned_invisible": False, "signal": 1,
        "format": fmt.DEFAULT_FORMAT, "format_colors": {},
    }
    base.update(kw)
    return base


def test_literal_text_passes_through():
    assert fmt.render(account(format="hello"), 1) == "hello"


def test_double_percent_is_a_literal_percent():
    assert fmt.render(account(format="100%%"), 1) == "100%"


def test_identity_tokens():
    a = account()
    assert fmt.render(a, 3, format_override="%name") == "<span size='110%'>work</span>"
    assert fmt.render(a, 3, format_override="%email") == "<span size='110%'>w@example.com</span>"
    assert fmt.render(a, 3, format_override="%index") == "<span size='110%'>3</span>"
    assert fmt.render(a, 3, format_override="%icon") == ICON


def test_icon_honours_hide_icon():
    """hide_icon keeps the glyph's width but drops its ink — the same alpha='1'
    trick label.render uses, so the bar does not jump when it is toggled."""
    out = fmt.render(account(hide_icon=True), 1, format_override="%icon")
    assert out == "<span size='150%' rise='-800' alpha='1'>✻</span>"


def test_a_cleared_nickname_renders_empty():
    """The rule label.render already keeps: a cleared nickname means "show no
    text", not "show my email address"."""
    assert fmt.render(account(nickname=None), 1, format_override="%name") == ""
    assert fmt.render(account(nickname=""), 1, format_override="%name") == ""


def test_text_is_escaped():
    assert fmt.render(account(nickname="a<b&c"), 1, format_override="%name") \
        == "<span size='110%'>a&lt;b&amp;c</span>"


def test_unknown_tokens_render_literally():
    """Never blank. A Waybar module that renders nothing is indistinguishable
    from a crashed one, so a typo must name itself on screen."""
    assert fmt.render(account(format="%bogus"), 1) == "%bogus"
    assert fmt.unknown_tokens("%name %bogus %nope") == ["%bogus", "%nope"]
    assert fmt.unknown_tokens("%name %email") == []


def test_tokens_in_is_ordered_and_deduplicated():
    assert fmt.tokens_in("%icon %name %icon %email") == ["%icon", "%name", "%email"]


def test_empty_tokens_collapse_the_space_around_them():
    """An unwired hook must not leave a stray gap in the middle of the label."""
    assert fmt.render(account(nickname=None), 1, format_override="%icon %name %email") \
        == f"{ICON} <span size='110%'>w@example.com</span>"
