import json
import importlib
import pytest

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
    assert a["color"] == 0 and a["signal"] == 1
    assert a["display"] == "nickname" and a["hide_icon"] is False
    assert a["warned_invisible"] is False
    assert reg["default"] == "personal"
    b = registry.add(reg, "work", "w@example.com", "work")
    assert b["color"] == 1 and b["signal"] == 2
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
    registry.set_field(reg, "a", "display", "icon only")
    assert registry.find(reg, "a")["display"] == "icon only"
    with pytest.raises(ValueError):
        registry.set_field(reg, "a", "display", "nonsense")
    with pytest.raises(ValueError):
        registry.set_field(reg, "a", "color", 99)


def test_colour_assignment_wraps_past_palette_end():
    reg = registry.load()
    for i in range(10):
        registry.add(reg, f"a{i}", f"a{i}@example.com", None)
    assert all(0 <= a["color"] < 8 for a in reg["accounts"])
