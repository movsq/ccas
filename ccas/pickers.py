"""fuzzel (GUI) and fzf (TTY) selection front-ends."""
import subprocess
import sys

# Shown as the only row of a free-text fuzzel prompt. fuzzel exits immediately
# when its stdin is empty, so a prompt with no rows can never be typed into;
# and because fuzzel echoes unmatched input verbatim, one throwaway row is
# enough to keep it open while the user types.
HINT = "(type a name, or Esc to skip)"

# Catppuccin green, matching the palette in paths.PALETTE. fuzzel's stock
# message colour is a washed-out grey that reads as disabled text, and the
# whole point of this line is that it stands out from the rows below it.
CREATE_COLOR = "a6e3a1ff"
CREATE_ROW = "— press Enter to create project"


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


def choose(prompt: str, rows, gui: bool, note=None):
    """Pick one row, or None.

    `note` is text above the rows that is not one of them — fuzzel's --mesg and
    fzf's --header, both of which honour newlines. It is what the retired
    GtkMenu did with an insensitive title row, and the reason a status line can
    be shown here without becoming something the user can accidentally select.
    """
    rows = list(rows)
    if not rows:
        return None
    if gui:
        cmd = ["fuzzel", "--dmenu", "--prompt", f"{prompt} ", "--width", "80",
               "--lines", "20"]
        if note:
            cmd += ["--mesg", note]
    else:
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


def prompt_or_clear(message: str, clear_label: str, gui: bool):
    """Free text, an explicit clear (None), or CANCEL.

    fuzzel needs at least one row to stay open at all, so the row it needs is
    put to work as the clear button: type and press Enter for a new value,
    select the row to clear, Esc to leave everything alone.
    """
    if gui:
        cmd = ["fuzzel", "--dmenu", "--prompt", f"{message} ", "--lines", "1",
               "--width", str(max(len(clear_label), len(message)) + 12)]
        proc = subprocess.run(cmd, input=clear_label, capture_output=True, text=True)
        if proc.returncode != 0:
            return CANCEL
        text = proc.stdout.strip()
        if not text:
            return CANCEL
        return None if text == clear_label else text
    try:
        text = input(f"{message} (blank cancels, '-' clears) ").strip()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    if not text:
        return CANCEL
    return None if text == "-" else text


def confirm_create(display_path: str, gui: bool) -> bool:
    """Ask before launching into a project directory that does not exist yet.

    Ideally this would be inline in the project picker: a green echo of the
    path forming underneath as it is typed. fuzzel cannot do that — its dmenu
    rows are fixed when stdin closes, there is no live-input hook and no markup
    mode, so nothing can react to keystrokes. This is the same idea one window
    later: the path in green above, the confirm row below in the theme's own
    off-white.

    --only-match matters. Without it fuzzel echoes unmatched input verbatim,
    and any non-empty stdout here would read as consent to create a directory.
    """
    if gui:
        # fuzzel's default 30 characters truncates both the row and any real
        # project path, and a half-shown path is the one thing here that must
        # be readable — it is what the user is agreeing to create.
        width = max(len(CREATE_ROW), len(display_path)) + 4
        cmd = ["fuzzel", "--dmenu", "--index", "--only-match", "--hide-prompt",
               "--mesg", display_path, "--message-color", CREATE_COLOR,
               "--lines", "2", "--width", str(width)]
        proc = subprocess.run(cmd, input=f"{CREATE_ROW}\nCancel",
                              capture_output=True, text=True)
        return proc.returncode == 0 and proc.stdout.strip() == "0"
    try:
        answer = input(f"{display_path} — create project? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def prompt(message: str, gui: bool):
    if gui:
        cmd = ["fuzzel", "--dmenu", "--prompt", f"{message} ", "--lines", "1"]
        proc = subprocess.run(cmd, input=HINT, capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        text = proc.stdout.strip()
        return None if not text or text == HINT else text
    try:
        return input(f"{message} ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None
