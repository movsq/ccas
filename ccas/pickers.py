"""fuzzel (GUI) and fzf (TTY) selection front-ends."""
import subprocess
import sys

# Shown as the only row of a free-text fuzzel prompt. fuzzel exits immediately
# when its stdin is empty, so a prompt with no rows can never be typed into;
# and because fuzzel echoes unmatched input verbatim, one throwaway row is
# enough to keep it open while the user types.
HINT = "(type a name, or Esc to skip)"


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


def choose(prompt: str, rows, gui: bool):
    rows = list(rows)
    if not rows:
        return None
    if gui:
        cmd = ["fuzzel", "--dmenu", "--prompt", f"{prompt} ", "--width", "80",
               "--lines", "20"]
    else:
        cmd = ["fzf", "--prompt", f"{prompt} ", "--height", "40%", "--reverse"]
    proc = subprocess.run(cmd, input="\n".join(rows), capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    choice = proc.stdout.strip("\n")
    return choice or None


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
