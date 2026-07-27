import json
import importlib
import pytest

import ccas.format as fmt
import ccas.paths as paths
import ccas.registry as registry


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    importlib.reload(paths)
    importlib.reload(registry)


def test_slugify_produces_safe_identifiers():
    assert registry.slugify("Work Account") == "work-account"
    assert registry.slugify("someone@example.com") == "someone"
    assert registry.slugify("  Ünïcode!! ") == "unicode"
    assert registry.slugify("!!!") == "account"


def test_load_returns_empty_registry_when_absent():
    reg = registry.load()
    assert reg == {"default": None, "accounts": []}


def test_add_assigns_colour_signal_and_default():
    reg = registry.load()
    a = registry.add(reg, "personal", "p@example.com", None)
    assert fmt.valid_color(a["color"]) and a["signal"] == 1
    assert "display" not in a and a["hide_icon"] is False
    assert a["warned_invisible"] is False
    assert reg["default"] == "personal"
    b = registry.add(reg, "work", "w@example.com", "work")
    assert fmt.valid_color(b["color"]) and b["signal"] == 2
    assert reg["default"] == "personal"


def test_save_and_load_round_trip():
    reg = registry.load()
    registry.add(reg, "personal", "p@example.com", "mine")
    registry.save(reg)
    again = registry.load()
    assert again["accounts"][0]["nickname"] == "mine"
    assert again["default"] == "personal"


def test_save_is_atomic_leaving_no_temp_files():
    reg = registry.load()
    registry.add(reg, "personal", "p@example.com", None)
    registry.save(reg)
    leftovers = [p.name for p in paths.accounts_root().iterdir() if p.name != "accounts.json"]
    assert leftovers == []


def test_remove_renumbers_signals_and_reassigns_default():
    reg = registry.load()
    registry.add(reg, "a", "a@example.com", None)
    registry.add(reg, "b", "b@example.com", None)
    registry.add(reg, "c", "c@example.com", None)
    registry.remove(reg, "a")
    assert [x["slug"] for x in reg["accounts"]] == ["b", "c"]
    assert [x["signal"] for x in reg["accounts"]] == [1, 2]
    assert reg["default"] == "b"


def test_remove_last_account_clears_default():
    reg = registry.load()
    registry.add(reg, "solo", "s@example.com", None)
    registry.remove(reg, "solo")
    assert reg["default"] is None
    assert reg["accounts"] == []


def test_index_of_is_one_based():
    reg = registry.load()
    registry.add(reg, "a", "a@example.com", None)
    registry.add(reg, "b", "b@example.com", None)
    assert registry.index_of(reg, "b") == 2


def test_set_field_rejects_invalid_values():
    reg = registry.load()
    registry.add(reg, "a", "a@example.com", None)
    with pytest.raises(ValueError):
        registry.set_field(reg, "a", "color", 99)


def test_colour_assignment_never_runs_out():
    """It used to walk paths.PALETTE and there were eight of them. A random hue
    has no end to reach, which is the point of it."""
    reg = registry.load()
    for i in range(10):
        registry.add(reg, f"a{i}", f"a{i}@example.com", None)
    assert all(fmt.valid_color(a["color"]) for a in reg["accounts"])


def test_load_backfills_the_headless_flag_on_older_registries():
    """accounts.json files written before the headless runner existed have no
    such key, and every read path assumes it is there."""
    paths.accounts_root().mkdir(parents=True, exist_ok=True)
    paths.registry_file().write_text(json.dumps({
        "default": "work",
        "accounts": [{"slug": "work", "nickname": None, "email": "w@x.com",
                      "color": 0, "display": "nickname", "hide_icon": False,
                      "warned_invisible": False, "signal": 1}],
    }), encoding="utf-8")
    assert registry.load()["accounts"][0]["headless"] is False


def test_set_headless_is_exclusive():
    """Exactly one account runs `claude -p`, so setting it must clear the rest."""
    reg = registry.load()
    for slug in ("a", "b", "c"):
        registry.add(reg, slug, f"{slug}@x.com", slug)

    registry.set_headless(reg, "b")
    assert [a["headless"] for a in reg["accounts"]] == [False, True, False]
    registry.set_headless(reg, "c")
    assert [a["headless"] for a in reg["accounts"]] == [False, False, True]
    assert registry.headless_slug(reg) == "c"


def test_set_headless_to_none_clears_it():
    reg = registry.load()
    registry.add(reg, "a", "a@x.com", "a")
    registry.set_headless(reg, "a")
    registry.set_headless(reg, None)
    assert registry.headless_slug(reg) is None


def test_removing_the_headless_account_leaves_none_set():
    """Otherwise the next `claude -p` would resolve to a deleted account."""
    reg = registry.load()
    registry.add(reg, "a", "a@x.com", "a")
    registry.add(reg, "b", "b@x.com", "b")
    registry.set_headless(reg, "b")
    registry.remove(reg, "b")
    assert registry.headless_slug(reg) is None


def test_new_accounts_are_not_dangerous():
    """--dangerously-skip-permissions is opt-in, per account, always."""
    reg = registry.load()
    account = registry.add(reg, "a", "a@x.com", "a")
    assert account["dangerous"] is False


def test_load_backfills_the_dangerous_flag_on_older_registries():
    """Same shape as the headless backfill: every read path assumes the key is
    there, and registries written before this feature have no such key."""
    paths.accounts_root().mkdir(parents=True, exist_ok=True)
    paths.registry_file().write_text(json.dumps({
        "default": "work",
        "accounts": [{"slug": "work", "nickname": None, "email": "w@x.com",
                      "color": 0, "display": "nickname", "hide_icon": False,
                      "headless": False, "warned_invisible": False, "signal": 1}],
    }), encoding="utf-8")
    assert registry.load()["accounts"][0]["dangerous"] is False


def test_dangerous_is_not_exclusive():
    """Unlike headless, which picks one runner, this is a per-account property —
    two accounts may both bypass permissions."""
    reg = registry.load()
    for slug in ("a", "b"):
        registry.add(reg, slug, f"{slug}@x.com", slug)
    registry.set_field(reg, "a", "dangerous", True)
    registry.set_field(reg, "b", "dangerous", True)
    assert [a["dangerous"] for a in reg["accounts"]] == [True, True]


# ── the format string and its two fields ──────────────────────────────────────

def test_a_new_accounts_colours_are_all_literal_hexes():
    """The preset the user asked for — glyph and used-percentage in one random
    colour, the address grey — with no indirection to reach it. The glyph
    follows `color` by definition, so only %5hused needs the hex written in."""
    reg = registry.load()
    a = registry.add(reg, "work", "w@e.com", None)
    assert fmt.HEX.match(a["color"])
    assert a["format"] == fmt.DEFAULT_FORMAT
    assert a["format_colors"] == {"%email": fmt.GREY, "%5hused": a["color"]}
    assert all(fmt.HEX.match(v) for v in a["format_colors"].values())


def test_two_new_accounts_do_not_share_a_colour():
    reg = registry.load()
    first = registry.add(reg, "a", "a@e.com", None)
    second = registry.add(reg, "b", "b@e.com", None)
    assert first["color"] != second["color"]


def test_set_field_validates_the_format():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format", "%name %5hused")
    assert registry.find(reg, "work")["format"] == "%name %5hused"
    with pytest.raises(ValueError):
        registry.set_field(reg, "work", "format", 7)


def test_an_unknown_token_is_stored_not_rejected():
    """Rejecting would mean that retiring a token in a later version turns a
    stored format into a hard error with nothing on the bar. Rendering it
    literally turns the same event into visible, self-explaining text."""
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format", "%name %bogus")
    assert registry.find(reg, "work")["format"] == "%name %bogus"


def test_set_field_keeps_everything_but_a_hex_out_of_the_colours():
    """Nothing but a hex is ever stored — that is what keeps the format string
    layout rather than markup. A dict is still the only shape the field takes,
    so that stays a ValueError; a *value* inside one is dropped instead, which
    is the same answer the read path gives it."""
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format_colors", {"%name": "#f9e2af"})
    for bad in ({"%name": "red"}, {"%name": "' x='y"}):
        registry.set_field(reg, "work", "format_colors", bad)
        assert registry.find(reg, "work")["format_colors"] == {}
    with pytest.raises(ValueError):
        registry.set_field(reg, "work", "format_colors", "not a dict")


def test_a_retired_colour_does_not_block_writing_a_different_token():
    """The bug this pins: `set_field` validated every value in the dict, and
    both write paths read the stored dict, change one key and hand the whole
    thing back. So one token still holding `account` — the retired value the
    read path deliberately tolerates — made *every other* token in that
    account unwritable, with rc 1 and no message and `doctor` green. A live,
    previewed, inert colour control is exactly what retiring `account` was
    meant to end; validating on write moved it up one level.

    Dropping rather than raising is the same rule the renderer already
    follows: not a hex means absent. It also self-heals — the stale value is
    gone the first time anything writes that account.
    """
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    stored = {"%5hreset": "#c7c7c7", "%5hquotaleft": "account"}
    registry.find(reg, "work")["format_colors"] = stored

    colors = dict(stored)
    colors["%5hreset"] = "#ff0000"
    registry.set_field(reg, "work", "format_colors", colors)
    assert registry.find(reg, "work")["format_colors"] == {"%5hreset": "#ff0000"}


def _write_registry(reg: dict) -> None:
    """The on-disk shape, for the read-path migrations. Matches what the two
    older tests above do by hand; they predate needing it more than once."""
    paths.accounts_root().mkdir(parents=True, exist_ok=True)
    paths.registry_file().write_text(json.dumps(reg), encoding="utf-8")


def test_add_gives_each_account_its_own_colour():
    """Not the next palette entry: with eight of them and two accounts that was
    a fixed pair, and the sliders made the palette a table of starting points
    rather than the set of colours an account can have."""
    reg = {"default": None, "accounts": []}
    colors = {registry.add(reg, f"a{i}", f"a{i}@x", None)["color"]
              for i in range(12)}
    assert len(colors) == 12


def test_set_field_accepts_a_hex_colour():
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    registry.set_field(reg, "a", "color", "#123abc")
    assert registry.find(reg, "a")["color"] == "#123abc"


def test_set_field_normalises_an_integer_colour():
    """`ccs color <slug> 3` predates hex storage and still has to land."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    registry.set_field(reg, "a", "color", 3)
    assert registry.find(reg, "a")["color"] == paths.PALETTE[3][1]


def test_set_field_rejects_a_token_colour_value():
    """auto/dim/account mean something for a token and nothing for the widget's
    own colour, so the widget validates against HEX and not valid_color."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    for bad in ("auto", "dim", "account", "#12", "blue"):
        with pytest.raises(ValueError):
            registry.set_field(reg, "a", "color", bad)






def test_set_field_rejects_display():
    """The field is retired, and nothing strips it on read any more — so the
    one place it could still get back in is the one that has to refuse it."""
    reg = {"default": None, "accounts": []}
    registry.add(reg, "a", "a@x", None)
    with pytest.raises(ValueError):
        registry.set_field(reg, "a", "display", "custom")


def test_add_writes_the_default_format_colours():
    reg = {"default": None, "accounts": []}
    a = registry.add(reg, "a", "a@x", None)
    assert a["format"] == fmt.DEFAULT_FORMAT
    assert a["format_colors"] == fmt.default_format_colors(a["color"])
    # A fresh dict per account, because the colour in it is per account: two
    # accounts editing one shared dict is not a thing that can happen now.
    b = registry.add(reg, "b", "b@x", None)
    assert a["format_colors"] is not b["format_colors"]


def test_a_new_accounts_format_colours_survive_set_field():
    """They are written by add() and validated by set_field, so the two have to
    agree on what a colour is — `account` failed that pair the moment
    valid_color stopped accepting it."""
    reg = {"default": None, "accounts": []}
    a = registry.add(reg, "a", "a@x", None)
    registry.set_field(reg, "a", "format_colors", dict(a["format_colors"]))
