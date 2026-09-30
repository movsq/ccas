"""Account directory lifecycle: create, relink, seed, trash."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import paths


def create(slug: str) -> Path:
    directory = paths.account_dir(slug)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def relink(slug: str) -> None:
    """Symlink every shared entry of ~/.claude; prune dangling links.

    Only ever reads ~/.claude. Never writes to it. This is the guarantee the
    whole design rests on, so nothing in this function may open a path under
    claude_home() for writing, create entries there, or follow a link out of
    the account directory before unlinking.
    """
    directory = create(slug)
    home = paths.claude_home()
    if not home.is_dir():
        return

    for entry in home.iterdir():
        if entry.name in paths.BLOCKLIST:
            continue
        target = directory / entry.name
        if target.is_symlink():
            if target.resolve() == entry.resolve():
                continue
            target.unlink()
        elif target.exists():
            continue  # a real per-account file wins
        target.symlink_to(entry)

    # A link made before its name joined the blocklist goes too. Only the link:
    # unlink() on a symlink never follows it into ~/.claude.
    for entry in directory.iterdir():
        if entry.is_symlink() and (not entry.exists()
                                   or entry.name in paths.BLOCKLIST):
            entry.unlink()


def rename(old: str, new: str) -> Path:
    """Move an account directory. Only safe before anything references it.

    `cmd_add` uses this in the one window where it cannot hurt: login has
    exited, the registry does not mention the account yet, and no Waybar module
    or menu names it. The symlinks inside point at absolute paths under
    ~/.claude, so moving the directory does not disturb them.
    """
    source, target = paths.account_dir(old), paths.account_dir(new)
    if target.exists():
        raise FileExistsError(target)
    source.rename(target)
    return target


def seed_config(slug: str) -> None:
    """Copy ~/.claude.json minus oauthAccount so trust and MCP carry over."""
    source = paths.claude_config_json()
    data = {}
    if source.exists():
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data.pop("oauthAccount", None)
    target = paths.account_dir(slug) / ".claude.json"
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(target, 0o600)


def to_trash(path: Path) -> Path:
    """Move to the checkout's .claude_trash. Never deletes."""
    trash = paths.trash_dir()
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = trash / f"ccas-{path.name}-{stamp}"
    counter = 1
    while target.exists():
        target = trash / f"ccas-{path.name}-{stamp}-{counter}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


# Markers saying "you are inside a Claude Code session". A session CCAS starts
# is a new top-level one, so none of them are true of it — and the first is
# actively harmful, because Claude Code reads it as "do not save a transcript".
# CCAS inherits them whenever it is launched from something that was itself
# started inside an agent shell: Waybar once, the GTK panel later. Scrubbing
# them here is what makes that impossible rather than merely unlikely.
PARENT_SESSION_VARS = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDECODE",
                       "CLAUDE_CODE_ENTRYPOINT")


def clean_env() -> dict:
    """This process's environment with the parent-session markers taken out.

    What to hand anything CCAS starts that is not itself a Claude Code session
    — a terminal, say. `env_for` is this plus an account.
    """
    env = dict(os.environ)
    for name in PARENT_SESSION_VARS:
        env.pop(name, None)
    return env


def env_for(slug: str) -> dict:
    env = clean_env()
    env["CLAUDE_CONFIG_DIR"] = str(paths.account_dir(slug))
    env["CCAS_INNER"] = "1"
    return env


def auth_status(slug: str) -> dict:
    try:
        proc = subprocess.run(
            [str(paths.claude_bin()), "auth", "status"],
            env=env_for(slug), capture_output=True, text=True, timeout=60,
        )
        return json.loads(proc.stdout)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {"loggedIn": False}
