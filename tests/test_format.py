import re

import ccas.format as fmt
import ccas.usage as usage

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
    assert fmt.unknown_tokens("%name %5h") == []


def test_tokens_in_is_ordered_and_deduplicated():
    assert fmt.tokens_in("%icon %name %icon %5h") == ["%icon", "%name", "%5h"]


def test_empty_tokens_collapse_the_space_around_them():
    """An unwired hook must not leave a stray gap in the middle of the label."""
    assert fmt.render(account(nickname=None), 1, format_override="%icon %name %email") \
        == f"{ICON} <span size='110%'>w@example.com</span>"


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
    being checked."""
    out = fmt.render(account(**account_kw), 1, usage, now, format_override=token)
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
        assert fmt.render(account(), 1, None, NOW, format_override=token) == ""


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


def test_auto_is_the_default_and_means_the_tokens_own_colour():
    r = reading(five_pct=96.0)
    out = fmt.render(account(), 1, r, NOW, format_override="%5hused")
    assert f"color='{usage.RED}'" in out


def test_a_named_colour_overrides_auto():
    r = reading(five_pct=96.0)
    a = account(format_colors={"%5hused": "#89b4fa"})
    out = fmt.render(a, 1, r, NOW, format_override="%5hused")
    assert "color='#89b4fa'" in out
    assert usage.RED not in out


def test_dim_is_an_alpha_not_a_colour():
    a = account(format_colors={"%name": "dim"})
    assert fmt.render(a, 1, format_override="%name") \
        == f"<span size='110%' alpha='{usage.DIM}'>work</span>"


def test_an_explicit_auto_is_the_same_as_absent():
    r = reading(five_pct=96.0)
    a = account(format_colors={"%5hused": "auto"})
    assert fmt.render(a, 1, r, NOW, format_override="%5hused") \
        == fmt.render(account(), 1, r, NOW, format_override="%5hused")


def test_the_icon_takes_an_override_too():
    a = account(format_colors={"%icon": "#a6e3a1"})
    assert fmt.render(a, 1, format_override="%icon") \
        == "<span size='150%' rise='-800' color='#a6e3a1'>✻</span>"


def test_hide_icon_wins_over_a_colour_override():
    """It is the invisibility toggle; a colour that resurrected the glyph would
    make the panel's checkbox lie."""
    a = account(hide_icon=True, format_colors={"%icon": "#a6e3a1"})
    assert fmt.render(a, 1, format_override="%icon") \
        == "<span size='150%' rise='-800' alpha='1'>✻</span>"


def test_valid_color():
    assert fmt.valid_color("auto") and fmt.valid_color("dim")
    assert fmt.valid_color("#f9e2af") and fmt.valid_color("#F9E2AF")
    assert not fmt.valid_color("f9e2af")
    assert not fmt.valid_color("red")
    assert not fmt.valid_color("#f9e2a")
    assert not fmt.valid_color("' foreground='x")   # no attribute injection


def test_the_default_format_reproduces_the_old_bar():
    """The migration canary, spelled out rather than compared against
    label.render — which no longer produces this, by design. Stripping the clock
    from the built-in modes is only safe because '%icon %name %5h' puts back
    exactly what they used to show."""
    r = reading()
    clock = usage.reset_clock(int(IN_2H20))
    assert fmt.render(account(), 1, r, NOW) == \
        f"{ICON} <span size='110%'>work</span> " \
        f"<span size='110%' color='{usage.YELLOW}'>{clock}</span>"


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


def test_account_is_a_valid_colour_value():
    assert fmt.valid_color("account")


def test_account_colours_a_token_with_the_widget_colour():
    """What makes the default's percentage follow the widget: one slider drag
    moves the glyph and the number together, with no hex baked into the
    registry to drift away from it."""
    a = account(color="#89b4fa", format="%name",
                format_colors={"%name": "account"}, nickname="n")
    assert "color='#89b4fa'" in fmt.render(a, 1)


def test_account_colours_the_icon_like_auto_does():
    """auto already meant the account's colour for the glyph; account is the
    same answer said explicitly, so the two agree rather than compete."""
    a = account(color="#f38ba8", format="%icon")
    explicit = fmt.render(dict(a, format_colors={"%icon": "account"}), 1)
    assert explicit == fmt.render(a, 1)
