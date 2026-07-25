"""Resolving a launch request into a working directory and argv.

resolve() is deliberately side-effect free so it can be tested; run() is the
thin layer that relinks, then either spawns kitty (GUI) or execs in place (TTY).
Every argv here starts with the absolute claude binary path. CCAS no longer
writes the `claude()` shell function (`ccs -p …` replaced it), but a shell opened
before that install still has the old one loaded, where a bare "claude" re-enters
it and loops forever. Absolute path, always.
"""
import os
import subprocess
import time

from . import accounts, history, paths, pickers, registry


def _rows():
    """(label, uuid, cwd) for the recent sessions, scanned live.

    ~/.claude/projects is shared across accounts, so this takes no slug. It was
    a per-account snapshot only because a GtkMenu item can carry a fixed action
    id and nothing else — `hist-3` had to mean line 3 of a file written at the
    same instant as the menu the user was looking at.
    """
    now = time.time()
    return [(history.format_row(s, now), s.uuid, s.cwd)
            for s in history.scan()[: paths.HIST_SLOTS]]


def _claude(*args, dangerous=False):
    # Before the mode's own arguments, never after: --resume takes a value and
    # the flag must not land between it and its uuid.
    flag = [DANGEROUS] if dangerous else []
    return [str(paths.claude_bin()), *flag, *args]


# claude's own spelling, verbatim. There is no short alias to mirror (the whole
# short-flag set is -c -d -h -n -p -r -v -w), and --allow-dangerously-skip-
# permissions is a different flag: it makes bypass available, not enabled.
DANGEROUS = "--dangerously-skip-permissions"


def resolve(slug: str, mode: str, arg, gui: bool, cwd, dangerous: bool = False):
    """Return (workdir, argv), or None when there is nothing to launch.

    `dangerous` is the account's opt-in to --dangerously-skip-permissions. It
    arrives as a parameter rather than a registry read so that resolve() stays
    side-effect free and testable; run() is what loads the registry.
    """
    if mode == "new":
        target = arg
        if target is None:
            sessions = history.scan()
            dirs = history.project_dirs(sessions)
            if cwd and cwd not in dirs:
                dirs.insert(0, cwd)
            choice = pickers.choose("project", [history.abbreviate(d) for d in dirs], gui)
            if choice is None:
                return None
            # The picker lists abbreviated paths and fuzzel echoes unmatched
            # input verbatim, so what comes back may be a ~ path the user typed
            # for a project that does not exist yet.
            target = os.path.expanduser(choice)
        if not os.path.isdir(target):
            if not pickers.confirm_create(history.abbreviate(target), gui):
                return None
        return target, _claude(dangerous=dangerous)

    rows = _rows()
    if cwd is not None and not gui:
        rows = [r for r in rows if r[2] == cwd]

    if mode == "last":
        if not rows:
            return None
        _label, uuid, session_cwd = rows[0]
        return session_cwd, _claude("--resume", uuid, dangerous=dangerous)

    if mode == "search":
        if not rows:
            return None
        labels = [r[0] for r in rows]
        choice = pickers.choose("resume", labels, gui)
        if choice is None:
            return None
        for label, uuid, session_cwd in rows:
            if label == choice:
                return session_cwd, _claude("--resume", uuid, dangerous=dangerous)
        return None

    raise ValueError(f"unknown mode: {mode}")


def run(slug: str, mode: str, arg, gui: bool, cwd) -> int:
    accounts.relink(slug)
    account = registry.find(registry.load(), slug)
    dangerous = bool(account and account.get("dangerous"))
    resolved = resolve(slug, mode, arg, gui, cwd, dangerous)
    if resolved is None:
        return 1
    workdir, argv = resolved
    if mode == "new":
        # resolve() has already confirmed anything that did not exist. Only
        # "new" creates: a resume whose directory has since been deleted should
        # fail loudly rather than come back as an empty resurrected folder.
        os.makedirs(workdir, exist_ok=True)
    env = accounts.env_for(slug)
    if gui:
        inner = " ".join(f"'{a}'" for a in argv)
        return subprocess.run(
            ["kitty", "--class", "ccas", "-e", "bash", "-lc", f"cd {workdir!r} && exec {inner}"],
            env=env, check=False,
        ).returncode
    os.chdir(workdir)
    os.execve(argv[0], argv, env)
