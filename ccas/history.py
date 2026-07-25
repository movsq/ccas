"""Reading session transcripts out of ~/.claude/projects."""
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths

HEAD = 64 * 1024

# Whitespace after the colon is tolerated: Claude Code writes compact JSON, but
# test fixtures built with json.dumps default to ": ". Both must match.
_CWD = re.compile(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TITLE = re.compile(rb'"aiTitle"\s*:\s*"((?:[^"\\]|\\.)*)"')


@dataclass
class Session:
    uuid: str
    cwd: str
    title: str
    mtime: float
    path: Path


def _decode(raw: bytes):
    """Decode a raw JSON string body, honouring \\uXXXX and surrogate pairs.

    Re-quoting and handing it to json.loads is the only correct approach here;
    a unicode_escape round-trip mangles non-Latin-1 text such as "Vojtěch".
    """
    try:
        return json.loads(b'"' + raw + b'"')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


def extract(path: Path):
    """Return (cwd, title). Reads a 64 KB head; full re-read only on a miss."""
    try:
        with open(path, "rb") as fh:
            buf = fh.read(HEAD)
    except OSError:
        return None, None
    title = _TITLE.search(buf)
    if title is None:
        try:
            with open(path, "rb") as fh:
                buf = fh.read()
        except OSError:
            return None, None
        title = _TITLE.search(buf)
    cwd = _CWD.search(buf)
    return (
        _decode(cwd.group(1)) if cwd else None,
        _decode(title.group(1)) if title else None,
    )


def scan():
    root = paths.projects_root()
    if not root.is_dir():
        return []
    out = []
    for project in root.iterdir():
        if not project.is_dir():
            continue
        for path in project.glob("*.jsonl"):
            cwd, title = extract(path)
            if not cwd:
                continue
            out.append(
                Session(
                    uuid=path.stem,
                    cwd=cwd,
                    title=title,
                    mtime=path.stat().st_mtime,
                    path=path,
                )
            )
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def project_dirs(sessions):
    seen = []
    for s in sessions:
        if s.cwd not in seen and os.path.isdir(s.cwd):
            seen.append(s.cwd)
    return seen


def humanise_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f}h" if hours < 10 else f"{int(hours)}h"
    return f"{int(seconds // 86400)}d"


def abbreviate(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def format_row(s: Session, now: float) -> str:
    label = s.title or f"(untitled) {time.strftime('%Y-%m-%d %H:%M', time.localtime(s.mtime))}"
    return f"{humanise_age(now - s.mtime):>5}  {abbreviate(s.cwd):<24}  {label}"
