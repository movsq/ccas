import importlib
import pytest

import ccas.pickers as pickers


def test_choose_returns_none_when_cancelled(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(pickers.subprocess, "run", lambda *a, **k: Result())
    assert pickers.choose("pick", ["a", "b"], gui=True) is None


def test_choose_returns_the_selected_row(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "second\n"

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    assert pickers.choose("pick", ["first", "second"], gui=True) == "second"
    assert "fuzzel" in seen["cmd"][0]
    assert seen["input"] == "first\nsecond"


def test_choose_uses_fzf_in_tty_mode(monkeypatch):
    class Result:
        returncode = 0
        stdout = "first\n"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    pickers.choose("pick", ["first"], gui=False)
    assert "fzf" in captured["cmd"][0]


def test_choose_with_no_rows_returns_none(monkeypatch):
    monkeypatch.setattr(pickers.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not spawn a picker"))
    assert pickers.choose("pick", [], gui=True) is None
