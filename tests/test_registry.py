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

def test_a_new_account_carries_the_default_format():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    account = registry.find(reg, "work")
    assert account["format"] == fmt.DEFAULT_FORMAT
    assert account["format_colors"] == fmt.DEFAULT_FORMAT_COLORS


def test_set_field_validates_the_format():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format", "%icon %5hused")
    assert registry.find(reg, "work")["format"] == "%icon %5hused"
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


def test_set_field_validates_the_colours():
    reg = registry.load()
    registry.add(reg, "work", "w@example.com", None)
    registry.set_field(reg, "work", "format_colors", {"%name": "#f9e2af"})
    for bad in ({"%name": "red"}, {"%name": "' x='y"}, "not a dict"):
        with pytest.raises(ValueError):
            registry.set_field(reg, "work", "format_colors", bad)


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
    assert a["format_colors"] == fmt.DEFAULT_FORMAT_COLORS
    # A copy, not the shared constant: two accounts must not edit one dict.
    assert a["format_colors"] is not fmt.DEFAULT_FORMAT_COLORS
