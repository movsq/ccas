"""The terminal door's front-ends. There is no other kind here any more.

The fuzzel half of every function below was retired with Task 12 — the GTK
panel is the GUI door and the only one. What went with it were fuzzel's
workarounds rather than anything the user could see: a placeholder row, because
fuzzel exits instantly on empty stdin; --only-match, because it echoes unmatched
input verbatim and stray text would have read as consent; a computed --width,
because its default 30 columns truncated a project path silently.
"""
import pathlib
import pytest

import ccas.pickers as pickers


def test_pickers_no_longer_shells_out_to_fuzzel():
    """A surviving fuzzel branch would be a second, unstyled front door that
    nothing routes to but everything would have to keep working."""
    source = pathlib.Path("ccas/pickers.py").read_text()
    assert "subprocess.run" in source, "the fzf half is still the real thing"
    assert '"fuzzel"' not in source


def test_choose_returns_none_when_cancelled(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(pickers.subprocess, "run", lambda *a, **k: Result())
    assert pickers.choose("pick", ["a", "b"]) is None


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
    assert pickers.choose("pick", ["first", "second"]) == "second"
    assert "fzf" in seen["cmd"][0]
    assert seen["input"] == "first\nsecond"


def test_choose_with_no_rows_returns_none(monkeypatch):
    monkeypatch.setattr(pickers.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not spawn a picker"))
    assert pickers.choose("pick", []) is None


def test_prompt_reads_a_line(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p: "  typed name ")
    assert pickers.prompt("nickname:") == "typed name"


def test_prompt_returns_none_when_cancelled(monkeypatch):
    def interrupted(_p):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    assert pickers.prompt("nickname:") is None
    monkeypatch.setattr("builtins.input", lambda _p: "")
    assert pickers.prompt("nickname:") is None


def test_prompt_or_clear_separates_cancel_from_clear(monkeypatch):
    """Three outcomes a plain prompt() cannot express: text, clear, cancel.

    Esc used to be indistinguishable from clearing, so backing out of a rename
    wiped the nickname it was backing out of.
    """
    monkeypatch.setattr("builtins.input", lambda _p: "  bob ")
    assert pickers.prompt_or_clear("nickname:", "clear it") == "bob"
    monkeypatch.setattr("builtins.input", lambda _p: "-")
    assert pickers.prompt_or_clear("nickname:", "clear it") is None
    monkeypatch.setattr("builtins.input", lambda _p: "")
    assert pickers.prompt_or_clear("nickname:", "clear it") is pickers.CANCEL


def test_prompt_or_clear_cancels_on_interruption(monkeypatch):
    def eof(_p):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert pickers.prompt_or_clear("nickname:", "clear it") is pickers.CANCEL


def test_confirm_create_asks_before_making_a_directory(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert pickers.confirm_create("~/code/new") is True
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert pickers.confirm_create("~/code/new") is False


def test_confirm_create_declines_on_interruption(monkeypatch):
    def interrupted(_p):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    assert pickers.confirm_create("~/code/new") is False


def _capture(monkeypatch, stdout="first\n"):
    class Result:
        returncode = 0
    Result.stdout = stdout
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    return seen


def test_a_note_is_shown_above_the_rows_and_cannot_be_picked(monkeypatch):
    """What the retired GtkMenu did with an insensitive title row. fzf's
    --header honours a newline, so a second line costs nothing."""
    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"], note="5h ≥94%\n7d ≥91%")
    assert seen["cmd"][seen["cmd"].index("--header") + 1] == "5h ≥94%\n7d ≥91%"


def test_no_note_leaves_the_command_as_it_was(monkeypatch):
    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"])
    assert "--header" not in seen["cmd"]


def test_is_gui_still_answers_which_door_was_used(monkeypatch):
    """It never chose a front end — it says whether there is a terminal on the
    other end, and Waybar's inherited stdin is why that has to be a fallback
    behind the explicit --gui flag."""
    monkeypatch.setattr(pickers.sys, "stdin",
                        type("S", (), {"isatty": lambda _s: True})())
    assert pickers.is_gui() is False
    monkeypatch.setattr(pickers.sys, "stdin",
                        type("S", (), {"isatty": lambda _s: False})())
    assert pickers.is_gui() is True
