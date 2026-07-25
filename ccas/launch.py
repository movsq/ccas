"""Resolving a launch request into a working directory and argv.

resolve() is deliberately side-effect free so it can be tested; run() is the
thin layer that relinks, then either spawns kitty (GUI) or execs in place (TTY).
Every argv here starts with the absolute claude binary path — a bare "claude"
would re-enter the shell function from ~/.bashrc and loop forever.
"""
import os
import subprocess

from . import accounts, history, menu, paths, pickers


def _rows(slug: str):
    return menu.read_tsv(slug)


def _claude(*args):
    return [str(paths.claude_bin()), *args]


def resolve(slug: str, mode: str, arg, gui: bool, cwd):
    """Return (workdir, argv), or None when there is nothing to launch."""
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
        return target, _claude()

    rows = _rows(slug)
    if cwd is not None and not gui:
        rows = [r for r in rows if r[2] == cwd]

    if mode == "last":
        if not rows:
            return None
        _label, uuid, session_cwd = rows[0]
        return session_cwd, _claude("--resume", uuid)

    if mode == "hist":
        try:
            index = int(arg)
        except (TypeError, ValueError):
            return None
        if not (0 <= index < len(rows)):
            return None
        _label, uuid, session_cwd = rows[index]
        return session_cwd, _claude("--resume", uuid)

    if mode == "search":
        if not rows:
            return None
        labels = [r[0] for r in rows]
        choice = pickers.choose("resume", labels, gui)
        if choice is None:
            return None
        for label, uuid, session_cwd in rows:
            if label == choice:
                return session_cwd, _claude("--resume", uuid)
        return None

    raise ValueError(f"unknown mode: {mode}")


def run(slug: str, mode: str, arg, gui: bool, cwd) -> int:
    accounts.relink(slug)
    resolved = resolve(slug, mode, arg, gui, cwd)
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
