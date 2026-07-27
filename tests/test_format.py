import re

import ccas.format as fmt
import ccas.usage as usage

# The glyph render() puts ahead of every format string, in the fixture
# account's own colour. Not a token any more, so there is one of these rather
# than a white one and an account-coloured one.
ICON = "<span size='150%' rise='-800' color='#f38ba8'>✻</span>"


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": "#f38ba8", "hide_icon": False,
        "warned_invisible": False, "signal": 1,
        "format": fmt.DEFAULT_FORMAT, "format_colors": {},
    }
    base.update(kw)
    return base


# hide_icon throughout the token tests: they are about the format string, and
# the glyph is no longer part of it. The prefix has tests of its own below.
def test_literal_text_passes_through():
    assert fmt.render(account(hide_icon=True, format="hello"), 1) == "hello"


def test_double_percent_is_a_literal_percent():
    assert fmt.render(account(hide_icon=True, format="100%%"), 1) == "100%"


def test_identity_tokens():
    a = account(hide_icon=True)
    assert fmt.render(a, 3, format_override="%name") == "<span size='110%' color='#ffffff'>work</span>"
    assert fmt.render(a, 3, format_override="%email") == "<span size='110%' color='#ffffff'>w@example.com</span>"
    assert fmt.render(a, 3, format_override="%index") == "<span size='110%' color='#ffffff'>3</span>"


def test_the_glyph_is_drawn_ahead_of_the_format_string():
    """It was never layout. `%icon` and the panel's `the widget` were two names
    for one colour, which is what made the colour editor unreadable — you could
    set them to different values and only one of them was the bar."""
    a = account(color="#89b4fa", format="%name", nickname="n")
    out = fmt.render(a, 1)
    assert out.startswith(f"<span size='{fmt.ICON_SIZE}' "
                          f"rise='{fmt.ICON_RISE}' color='#89b4fa'>✻</span>")
    assert out.endswith("n</span>")


def test_the_glyph_follows_the_account_colour_with_no_token_to_override_it():
    a = account(color="#a6e3a1", format="%name", nickname="n",
                format_colors={"%icon": "#f38ba8"})
    assert "#a6e3a1" in fmt.render(a, 1)
    assert "#f38ba8" not in fmt.render(a, 1)


def test_hide_icon_removes_the_glyph_entirely():
    """No alpha='1' spacer: the glyph is no longer named in the format string,
    so an invisible one would reserve width in every label that hid it."""
    a = account(hide_icon=True, format="%name", nickname="n")
    out = fmt.render(a, 1)
    assert "✻" not in out
    assert out == f"<span size='{fmt.TEXT_SIZE}' " \
                  f"color='{fmt.DEFAULT_COLOR}'>n</span>"


def test_icon_is_not_a_token_and_renders_as_its_own_name():
    """A retired token renders literally rather than raising — visible and
    self-explaining, which is what tells the user to re-set their format."""
    assert "%icon" not in fmt.TOKENS
    assert fmt.unknown_tokens("%icon %name") == ["%icon"]
    assert fmt.tokens_in("%icon %name") == ["%name"]
    a = account(format="%icon", hide_icon=True)
    assert fmt.render(a, 1) == "%icon"


def test_a_cleared_nickname_renders_empty():
    """The rule label.render already keeps: a cleared nickname means "show no
    text", not "show my email address"."""
    a = account(hide_icon=True)
    assert fmt.render(dict(a, nickname=None), 1, format_override="%name") == ""
    assert fmt.render(dict(a, nickname=""), 1, format_override="%name") == ""


def test_text_is_escaped():
    assert fmt.render(account(hide_icon=True, nickname="a<b&c"), 1,
                      format_override="%name") \
        == "<span size='110%' color='#ffffff'>a&lt;b&amp;c</span>"


def test_unknown_tokens_render_literally():
    """Never blank. A Waybar module that renders nothing is indistinguishable
    from a crashed one, so a typo must name itself on screen."""
    assert fmt.render(account(hide_icon=True, format="%bogus"), 1) == "%bogus"
    assert fmt.unknown_tokens("%name %bogus %nope") == ["%bogus", "%nope"]
    assert fmt.unknown_tokens("%name %5h") == []


def test_tokens_in_is_ordered_and_deduplicated():
    assert fmt.tokens_in("%name %email %name %5h") == ["%name", "%email", "%5h"]


def test_empty_tokens_collapse_the_space_around_them():
    """An unwired hook must not leave a stray gap in the middle of the label."""
    assert fmt.render(account(nickname=None), 1, format_override="%name %email") \
        == f"{ICON} <span size='110%' color='#ffffff'>w@example.com</span>"


NOW = 1_800_000_000.0            # a fixed epoch, so the clocks are stable
IN_2H20 = NOW + 2 * 3600 + 20 * 60


def reading(five_pct=62.0, five_at=IN_2H20, seven_pct=None, seven_at=None):
    out = {"fetched_at": NOW, "source": "statusline",
           "five_hour": None, "seven_day": None}
    if five_pct is not None:
        out["five_hour"] = {"percent": five_pct, "resets_at": int(five_at)}
    if seven_pct is not None:
        out["seven_day"] = {"percent": seven_pct, "resets_at": int(seven_at)}
    return out


def bare(account_kw, token, usage=None, now=NOW):
    """The token's text with its span stripped — the markup is asserted
    separately in the colour tests, and repeating it here would hide the fact
    being checked. The glyph is hidden for the same reason: stripping the tags
    off it would leave a ✻ in front of every one of these."""
    out = fmt.render(account(hide_icon=True, **account_kw), 1, usage, now,
                     format_override=token)
    return re.sub(r"<[^>]+>", "", out)


def test_five_hour_tokens():
    r = reading()
    assert bare({}, "%5hreset", r) == usage.reset_clock(int(IN_2H20))
    assert bare({}, "%5htimeleft", r) == "2h20m"
    assert bare({}, "%5hused", r) == "62%"
    assert bare({}, "%5hquotaleft", r) == "38%"


def test_seven_day_tokens():
    """%7dreset is a weekday and a clock: a 7-day reset can be days out, and a
    bare HH:MM would be a lie about which day."""
    r = reading(seven_pct=100.0, seven_at=NOW + 2 * 86400 + 21 * 3600)
    assert bare({}, "%7dreset", r) == usage.reset_time(int(NOW + 2 * 86400 + 21 * 3600), NOW)
    assert bare({}, "%7dtimeleft", r) == "2d21h"
    assert bare({}, "%7dused", r) == "100%"
    assert bare({}, "%7dquotaleft", r) == "0%"


def test_timeleft_under_an_hour_drops_the_hours():
    assert bare({}, "%5htimeleft", reading(five_at=NOW + 48 * 60)) == "48m"


def test_timeleft_pads_the_minutes_beside_an_hour():
    """'2h5m' would sort and read as longer than '2h50m'; the pad keeps the
    field width fixed so the bar does not jiggle as the minutes tick down."""
    assert bare({}, "%5htimeleft", reading(five_at=NOW + 2 * 3600 + 5 * 60)) == "2h05m"


def test_an_absent_window_renders_every_one_of_its_tokens_empty():
    for token in ("%5hreset", "%5htimeleft", "%5hused", "%5hquotaleft", "%5h"):
        assert fmt.render(account(hide_icon=True), 1, None, NOW,
                          format_override=token) == ""


def test_an_open_window_has_no_clock_but_keeps_its_percentage():
    """resets_at in the past means the window rolled over, so the clock is
    meaningless — but the recorded percentage is still the last thing known."""
    r = reading(five_at=NOW - 60)
    assert bare({}, "%5hreset", r) == ""
    assert bare({}, "%5htimeleft", r) == ""
    assert bare({}, "%5hused", r) == "62%"


def test_the_smart_tokens_are_usage_bar():
    r = reading()
    assert bare({}, "%5h", r) == usage.bar(r, NOW)[0]
    seven = reading(five_pct=None, seven_pct=95.0, seven_at=NOW + 86400)
    assert bare({}, "%7d", seven) == "7d 95%"


def test_a_token_with_no_colour_of_its_own_is_white():
    """One answer for every token, whatever it renders. `auto` used to make it
    depend on the token — the usage ramp here, the account colour there — so
    the one thing a format string could not tell you was how it would look."""
    r = reading(five_pct=96.0)
    out = fmt.render(account(hide_icon=True), 1, r, NOW, format_override="%5hused")
    assert out == f"<span size='110%' color='{fmt.DEFAULT_COLOR}'>96%</span>"
    assert usage.RED not in out


def test_a_named_colour_overrides_the_default():
    r = reading(five_pct=96.0)
    # hide_icon, because the fixture's account colour *is* usage.RED and the
    # glyph would otherwise put it in every label here.
    a = account(hide_icon=True, format_colors={"%5hused": "#89b4fa"})
    out = fmt.render(a, 1, r, NOW, format_override="%5hused")
    assert "color='#89b4fa'" in out
    assert usage.RED not in out


def test_a_retired_auto_or_dim_reads_as_no_colour_at_all():
    """They were removed rather than migrated: there is one user and one
    installation, so a stored value nothing accepts any more simply falls to
    the default the same way an absent one does."""
    r = reading(five_pct=96.0)
    for retired in ("auto", "dim"):
        a = account(format_colors={"%5hused": retired})
        assert fmt.render(a, 1, r, NOW, format_override="%5hused") \
            == fmt.render(account(), 1, r, NOW, format_override="%5hused")


def test_hide_icon_wins_over_the_account_colour():
    """It is the invisibility toggle; a colour that resurrected the glyph would
    make the panel's checkbox lie."""
    a = account(hide_icon=True, color="#a6e3a1", format="%name", nickname="n")
    assert "✻" not in fmt.render(a, 1)


def test_valid_color():
    assert not fmt.valid_color("account")
    assert not fmt.valid_color("auto") and not fmt.valid_color("dim")
    assert fmt.valid_color("#f9e2af") and fmt.valid_color("#F9E2AF")
    assert not fmt.valid_color("f9e2af")
    assert not fmt.valid_color("red")
    assert not fmt.valid_color("#f9e2a")
    assert not fmt.valid_color("' foreground='x")   # no attribute injection


def test_a_format_from_an_older_ccas_still_renders():
    """Nothing migrates a format string. One naming a token that still exists
    keeps working; the colours it does not name are simply the default."""
    r = reading()
    clock = usage.reset_clock(int(IN_2H20))
    assert fmt.render(account(format="%name %5h",
                              format_colors={}), 1, r, NOW) == \
        f"{ICON} <span size='110%' color='#ffffff'>work</span> " \
        f"<span size='110%' color='#ffffff'>{clock}</span>"


def test_an_account_predating_the_feature_still_renders():
    """Accounts written before the two fields existed are read, not migrated —
    render defaults both with .get, so nothing has to rewrite accounts.json."""
    a = account()
    del a["format"], a["format_colors"]
    assert fmt.render(a, 1) == fmt.render(account(), 1)


def test_hex_to_hs_reads_the_palette_blue():
    """The two slider coordinates come out of a stored hex. #89b4fa is
    paths.PALETTE's blue, measured at H=217.2 S=91.9%."""
    hue, sat = fmt.hex_to_hs("#89b4fa")
    assert round(hue, 1) == 217.2
    assert round(sat, 3) == 0.919


def test_hs_to_hex_pins_lightness():
    """Saturation 0 is a grey, and the same grey at every hue: with L fixed
    there is no hue left to see. It is light enough to read on the bar's
    #353535, which is why lightness is pinned rather than offered."""
    assert fmt.hs_to_hex(0, 0.0) == "#c7c7c7"
    assert fmt.hs_to_hex(217.2, 0.0) == "#c7c7c7"


def test_hs_to_hex_round_trips_within_a_rounding_error():
    """8-bit channels cannot hold the exact angle back, so this is approximate
    by nature — a drag that moved nothing must not shift the colour visibly."""
    hue, sat = fmt.hex_to_hs(fmt.hs_to_hex(217.2, 0.919))
    assert abs(hue - 217.2) < 1.0
    assert abs(sat - 0.919) < 0.02


def test_hs_to_hex_wraps_hue_and_clamps_saturation():
    """The hue slider is a ring: 360 and 0 are the same colour, so a slider at
    either end must not produce two different reds."""
    assert fmt.hs_to_hex(360, 0.5) == fmt.hs_to_hex(0, 0.5)
    assert fmt.hs_to_hex(120, 1.5) == fmt.hs_to_hex(120, 1.0)
    assert fmt.hs_to_hex(120, -0.5) == fmt.hs_to_hex(120, 0.0)


def test_hs_to_hex_is_always_a_valid_color():
    """It feeds a pango attribute, so it has to satisfy the same gate a typed
    colour does."""
    for hue in range(0, 360, 37):
        assert fmt.valid_color(fmt.hs_to_hex(hue, 0.8))


def test_account_is_no_longer_a_colour():
    """The one indirection left after auto/dim went. A colour setting that
    might or might not reach the label is the same defect auto had: the user
    dragged `the widget` for ten minutes against an account whose tokens all
    held literal hexes, and nothing on screen ever moved."""
    assert not fmt.valid_color("account")
    assert not hasattr(fmt, "ACCOUNT")


def test_a_stored_account_renders_as_the_default_colour():
    """Nothing migrates: an unrecognised value reads as absent, exactly as a
    retired `auto` or `dim` does."""
    a = account(color="#f38ba8", format="%name", nickname="n",
                hide_icon=True, format_colors={"%name": "account"})
    assert f"color='{fmt.DEFAULT_COLOR}'" in fmt.render(a, 1)
    assert "#f38ba8" not in fmt.render(a, 1)


def test_the_default_format_and_its_colours():
    """The preset a new account gets. The glyph is not in it because the glyph
    is not a token; its colour is the account's."""
    assert fmt.DEFAULT_FORMAT == "%email %5hused"
    assert fmt.default_format_colors("#89b4fa") == {
        "%email": fmt.GREY, "%5hused": "#89b4fa"}
    assert not hasattr(fmt, "DEFAULT_FORMAT_COLORS")


def test_the_default_renders_glyph_and_percent_in_the_widget_colour():
    """The address recedes and the two coloured runs match, so changing the
    widget's colour moves the whole label rather than half of it."""
    a = account(color="#89b4fa", format=fmt.DEFAULT_FORMAT,
                format_colors=fmt.default_format_colors("#89b4fa"))
    out = fmt.render(a, 1, reading(five_pct=36.0), NOW)
    assert out.count("color='#89b4fa'") == 2
    assert f"color='{fmt.GREY}'" in out


def test_random_color_is_always_a_valid_colour_at_the_pinned_lightness():
    """It reaches a pango attribute like any other, and a new account must not
    be able to come out black, white, or invisible on the bar."""
    seen = {fmt.random_color() for _ in range(50)}
    assert all(fmt.valid_color(c) for c in seen)
    assert len(seen) > 25          # random, not a rotation through a palette
    for value in seen:
        _hue, sat = fmt.hex_to_hs(value)
        assert 0.5 < sat <= 1.0
