"""fzf and stdin selection front-ends, for the terminal door only.

The GUI door is the GTK panel now (`panel_ui.py`), so nothing here has a second
branch. What went with fuzzel were its workarounds, not features: a HINT row
existed because fuzzel exits instantly on empty stdin, and CREATE_ROW/
CREATE_COLOR because it has no markup mode and echoes unmatched input verbatim,
which made any stray text read as consent. A widget tree has none of those
constraints.

`is_gui()` stays. It never chose a front end here — it decides which door was
used, which is still a question.
"""
import subprocess
import sys


def is_gui() -> bool:
    """Fallback only — prefer the explicit --gui flag on generated commands.

    Waybar inherits stdin from the compositor, which on a TTY session is a real
    terminal (/dev/tty1). Sniffing isatty() therefore misreports Waybar clicks
    as terminal invocations, and the terminal path blocks on input() against a
    console the user cannot see.
    """
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def choose(prompt: str, rows, note=None):
    """Pick one row, or None.

    `note` is text above the rows that is not one of them — fzf's --header,
    which honours newlines. It is what the retired GtkMenu did with an
    insensitive title row, and the reason a status line can be shown here
    without becoming something the user can accidentally select.
    """
    rows = list(rows)
    if not rows:
        return None
    cmd = ["fzf", "--prompt", f"{prompt} ", "--height", "40%", "--reverse"]
    if note:
        cmd += ["--header", note]
    proc = subprocess.run(cmd, input="\n".join(rows), capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    choice = proc.stdout.strip("\n")
    return choice or None


# Distinct from None, which means "clear the value". Returning None for both
# made Esc wipe the nickname it was trying to back out of.
CANCEL = object()


def prompt_or_clear(message: str, clear_label: str):
    """Free text, an explicit clear (None), or CANCEL.

    `clear_label` is still taken so the caller names the clear action once, in
    the place that knows what is being cleared.
    """
    try:
        text = input(f"{message} (blank cancels, '-' clears) ").strip()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    if not text:
        return CANCEL
    return None if text == "-" else text


def confirm_create(display_path: str) -> bool:
    """Ask before launching into a project directory that does not exist yet."""
    try:
        answer = input(f"{display_path} — create project? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def prompt_edit(message: str, initial: str):
    """Free text with `initial` already typed into the line, or None to cancel.

    readline is what makes it an *edit* rather than a retype: a format string is
    a small change to a long line far more often than it is a new one. The hook
    fires once — readline calls it on the first redisplay and we clear it there,
    so a second prompt in the same process does not inherit the seed.

    Absent readline (it is stdlib but optional at build time) the seed is only
    shown, which degrades to a retype rather than to a traceback.
    """
    try:
        import readline
    except ImportError:
        readline = None
    if readline is not None:
        def seed():
            readline.insert_text(initial)
            readline.redisplay()
            readline.set_pre_input_hook(None)
        readline.set_pre_input_hook(seed)
    try:
        text = input(f"{message} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    finally:
        if readline is not None:
            readline.set_pre_input_hook(None)
    return text or None


def prompt(message: str):
    try:
        return input(f"{message} ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None
