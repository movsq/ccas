"""fuzzel (GUI) and fzf (TTY) selection front-ends."""
import subprocess
import sys


def is_gui() -> bool:
    return not sys.stdin.isatty()


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
        cmd = ["fuzzel", "--dmenu", "--prompt", f"{message} ", "--lines", "0"]
        proc = subprocess.run(cmd, input="", capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    try:
        return input(f"{message} ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None
