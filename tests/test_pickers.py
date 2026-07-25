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


def test_prompt_gives_fuzzel_a_row_so_it_stays_open(monkeypatch):
    """fuzzel exits instantly on empty stdin, so a free-text prompt with no
    rows can never be typed into — that was the placeholder's silent 'blink'."""
    seen = {}

    class Result:
        returncode = 0
        stdout = "typed name\n"

    def fake_run(cmd, **kwargs):
        seen["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    assert pickers.prompt("nickname:", gui=True) == "typed name"
    assert seen["input"], "fuzzel must be given at least one row"


def test_prompt_treats_the_hint_row_as_no_answer(monkeypatch):
    class Result:
        returncode = 0
        stdout = pickers.HINT + "\n"

    monkeypatch.setattr(pickers.subprocess, "run", lambda *a, **k: Result())
    assert pickers.prompt("nickname:", gui=True) is None


def test_prompt_returns_none_when_cancelled(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(pickers.subprocess, "run", lambda *a, **k: Result())
    assert pickers.prompt("nickname:", gui=True) is None


def test_prompt_or_clear_separates_cancel_from_clear(monkeypatch):
    """Three outcomes a plain prompt() cannot express: text, clear, cancel."""
    seen = {}

    class Result:
        returncode = 0
        stdout = "new name\n"

    def fake_run(cmd, **kwargs):
        seen["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=True) == "new name"
    assert seen["input"] == "clear it", "the clear row must be selectable"

    Result.stdout = "clear it\n"
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=True) is None

    Result.returncode, Result.stdout = 1, ""
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=True) is pickers.CANCEL


def test_prompt_or_clear_treats_empty_output_as_cancel(monkeypatch):
    class Result:
        returncode = 0
        stdout = "\n"

    monkeypatch.setattr(pickers.subprocess, "run", lambda *a, **k: Result())
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=True) is pickers.CANCEL


def test_prompt_or_clear_on_the_terminal(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p: "  bob ")
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=False) == "bob"
    monkeypatch.setattr("builtins.input", lambda _p: "-")
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=False) is None
    monkeypatch.setattr("builtins.input", lambda _p: "")
    assert pickers.prompt_or_clear("nickname:", "clear it", gui=False) is pickers.CANCEL


def _confirm_run(monkeypatch, returncode, stdout):
    seen = {}

    class Result:
        pass

    Result.returncode, Result.stdout = returncode, stdout

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr(pickers.subprocess, "run", fake_run)
    return seen


def test_confirm_create_accepts_the_first_row(monkeypatch):
    seen = _confirm_run(monkeypatch, 0, "0\n")
    assert pickers.confirm_create("~/code/new", gui=True) is True
    assert seen["input"].split("\n")[0] == pickers.CREATE_ROW


def test_confirm_create_shows_the_path_in_green_above_the_row(monkeypatch):
    """The path is the thing being decided, so it gets the colour, not the row."""
    seen = _confirm_run(monkeypatch, 0, "0\n")
    pickers.confirm_create("~/code/new", gui=True)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--mesg") + 1] == "~/code/new"
    assert cmd[cmd.index("--message-color") + 1] == pickers.CREATE_COLOR


def test_confirm_create_is_wide_enough_for_the_path(monkeypatch):
    """Measured live: fuzzel's default 30 columns cut the row to 'create proje…'."""
    long_path = "~/very/deeply/nested/place/for/a/brand/new/project"
    seen = _confirm_run(monkeypatch, 0, "0\n")
    pickers.confirm_create(long_path, gui=True)
    cmd = seen["cmd"]
    assert int(cmd[cmd.index("--width") + 1]) > len(long_path)


def test_confirm_create_declines_on_the_cancel_row(monkeypatch):
    _confirm_run(monkeypatch, 0, "1\n")
    assert pickers.confirm_create("~/code/new", gui=True) is False


def test_confirm_create_declines_when_dismissed(monkeypatch):
    _confirm_run(monkeypatch, 1, "")
    assert pickers.confirm_create("~/code/new", gui=True) is False


def test_confirm_create_refuses_free_text(monkeypatch):
    """Without --only-match fuzzel echoes typed text back, and any non-empty
    stdout here would read as consent to create a directory."""
    seen = _confirm_run(monkeypatch, 0, "0\n")
    pickers.confirm_create("~/code/new", gui=True)
    assert "--only-match" in seen["cmd"]


def test_confirm_create_asks_on_the_terminal(monkeypatch):
    monkeypatch.setattr(pickers.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not spawn fuzzel"))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert pickers.confirm_create("~/code/new", gui=False) is True
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert pickers.confirm_create("~/code/new", gui=False) is False


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
    """What the retired GtkMenu did with an insensitive title row. Both
    front-ends have a place for text that is not a choice — and both honour a
    newline in it, so a second line costs nothing."""
    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"], gui=True, note="5h ≥94%\n7d ≥91%")
    assert seen["cmd"][seen["cmd"].index("--mesg") + 1] == "5h ≥94%\n7d ≥91%"

    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"], gui=False, note="5h ≥94%")
    assert seen["cmd"][seen["cmd"].index("--header") + 1] == "5h ≥94%"


def test_no_note_leaves_the_command_as_it_was(monkeypatch):
    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"], gui=True)
    assert "--mesg" not in seen["cmd"]
    seen = _capture(monkeypatch)
    pickers.choose("pick", ["first"], gui=False)
    assert "--header" not in seen["cmd"]
