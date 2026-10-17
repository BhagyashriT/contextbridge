"""Core logic for ContextBridge: project init, handoff save/load, and quick updates.

All persistence lives under ``.contextbridge/`` in the project root:

    .contextbridge/
        config.yaml            # project name + handoff dir
        handoffs/
            <id>.md            # one handoff per file: YAML front matter + Markdown body
        latest.md              # copy of the most recent handoff

Stdlib only. The front matter is a constrained YAML subset written and parsed
by this module (no PyYAML dependency).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONFIG_DIR = ".contextbridge"
HANDOFFS_DIR = "handoffs"
CONFIG_FILE = "config.yaml"
LATEST_FILE = "latest.md"
FRONT_MATTER_DELIM = "---"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """An unfinished task with a simple open/done status."""
    text: str
    status: str = "open"

    def is_open(self) -> bool:
        return self.status == "open"


@dataclass
class ReferencedFile:
    """A file worth re-reading next session."""
    path: str
    note: str = ""
    auto_detected: bool = False


@dataclass
class Handoff:
    """One structured session handoff."""
    id: str
    timestamp: str
    project: str
    summary: str
    decisions: List[str] = field(default_factory=list)
    open_tasks: List[Task] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    referenced_files: List[ReferencedFile] = field(default_factory=list)
    session_notes: str = ""


# ---------------------------------------------------------------------------
# Project layout helpers
# ---------------------------------------------------------------------------

def find_root(start: Optional[str] = None) -> str:
    """Return the nearest ancestor dir containing ``.contextbridge/``."""
    here = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(here, CONFIG_DIR)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            raise HandoffError(
                f"no {CONFIG_DIR}/ found — run `cb init` in your project first"
            )
        here = parent


def handoffs_dir(root: str) -> str:
    return os.path.join(root, CONFIG_DIR, HANDOFFS_DIR)


def read_config(root: str) -> Dict[str, str]:
    """Parse the tiny ``key: value`` config file."""
    cfg: Dict[str, str] = {"project": os.path.basename(root),
                           "handoff_dir": HANDOFFS_DIR}
    path = os.path.join(root, CONFIG_DIR, CONFIG_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                cfg[key.strip()] = value.strip().strip('"')
    return cfg


class HandoffError(Exception):
    """User-facing ContextBridge error."""


# ---------------------------------------------------------------------------
# Mini YAML front matter (write + parse the constrained subset we emit)
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return '"' + (text.replace("\\", "\\\\")
                      .replace('"', '\\"')
                      .replace("\n", "\\n")) + '"'


def _unesc(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return (text[1:-1].replace('\\"', '"')
                            .replace("\\\\", "\\")
                            .replace("\\n", "\n"))
    return text


def dump_front_matter(h: Handoff) -> str:
    """Serialize a handoff to its YAML front-matter block."""
    lines = [FRONT_MATTER_DELIM]
    lines.append(f"id: {_esc(h.id)}")
    lines.append(f"timestamp: {_esc(h.timestamp)}")
    lines.append(f"project: {_esc(h.project)}")
    lines.append(f"summary: {_esc(h.summary)}")
    lines.append(f"session_notes: {_esc(h.session_notes)}")

    def _scalar_list(key: str, values: List[str]) -> None:
        lines.append(f"{key}:")
        if not values:
            lines.append("  []")
        for v in values:
            lines.append(f"  - {_esc(v)}")

    _scalar_list("decisions", h.decisions)
    _scalar_list("constraints", h.constraints)

    lines.append("open_tasks:")
    if not h.open_tasks:
        lines.append("  []")
    for t in h.open_tasks:
        lines.append(f"  - text: {_esc(t.text)}")
        lines.append(f"    status: {_esc(t.status)}")

    lines.append("referenced_files:")
    if not h.referenced_files:
        lines.append("  []")
    for f in h.referenced_files:
        lines.append(f"  - path: {_esc(f.path)}")
        lines.append(f"    note: {_esc(f.note)}")
        lines.append(f"    auto_detected: {'true' if f.auto_detected else 'false'}")

    lines.append(FRONT_MATTER_DELIM)
    return "\n".join(lines) + "\n"


def parse_front_matter(text: str) -> Dict[str, object]:
    """Parse the constrained YAML front matter emitted by :func:`dump_front_matter`."""
    parts = text.split(FRONT_MATTER_DELIM)
    if len(parts) < 3:
        raise HandoffError("handoff file is missing its front-matter block")
    body = parts[1]

    data: Dict[str, object] = {}
    current_key: Optional[str] = None
    current_item: Optional[Dict[str, str]] = None

    def _flush_item() -> None:
        nonlocal current_item
        if current_item is not None and current_key:
            items = data.setdefault(current_key, [])
            assert isinstance(items, list)
            items.append(current_item)
        current_item = None

    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":") and not stripped.startswith("-"):
            _flush_item()
            current_key = stripped[:-1]
            if current_key not in data:
                data[current_key] = [] if current_key in (
                    "decisions", "open_tasks", "constraints", "referenced_files") else ""
        elif indent == 2 and stripped.startswith("- ") and current_key:
            _flush_item()
            rest = stripped[2:].strip()
            # NOTE: scalar items are always double-quoted by dump_front_matter,
            # while mapping keys never are — so a quoted value containing ": "
            # (e.g. - "note: buy milk") must NOT be treated as a mapping item.
            if (": " in rest or rest.endswith(":")) and not rest.startswith('"'):
                # start of a mapping item
                current_item = {}
                k, _, v = rest.partition(":")
                current_item[k.strip()] = _unesc(v)
            else:
                items = data.setdefault(current_key, [])
                assert isinstance(items, list)
                items.append(_unesc(rest))
        elif indent == 2 and stripped == "[]":
            _flush_item()
            data[current_key] = []  # type: ignore[index]
        elif indent == 4 and current_item is not None:
            k, _, v = stripped.partition(":")
            current_item[k.strip()] = _unesc(v)
        elif indent == 0 and ":" in stripped and current_key is None:
            k, _, v = stripped.partition(":")
            data[k.strip()] = _unesc(v)
        elif indent == 0 and ":" in stripped:
            # scalar value for a previously-seen key (e.g. summary: ...)
            _flush_item()
            k, _, v = stripped.partition(":")
            data[k.strip()] = _unesc(v)
    _flush_item()
    return data


def handoff_from_dict(data: Dict[str, object]) -> Handoff:
    """Rebuild a :class:`Handoff` from parsed front matter."""
    tasks = [Task(text=str(t.get("text", "")), status=str(t.get("status", "open")))
              for t in data.get("open_tasks", [])]  # type: ignore[union-attr]
    files = [ReferencedFile(path=str(f.get("path", "")),
                            note=str(f.get("note", "")),
                            auto_detected=str(f.get("auto_detected", "false")).lower() == "true")
             for f in data.get("referenced_files", [])]  # type: ignore[union-attr]
    return Handoff(
        id=str(data.get("id", "")),
        timestamp=str(data.get("timestamp", "")),
        project=str(data.get("project", "")),
        summary=str(data.get("summary", "")),
        decisions=[str(d) for d in data.get("decisions", [])],  # type: ignore[union-attr]
        open_tasks=tasks,
        constraints=[str(c) for c in data.get("constraints", [])],  # type: ignore[union-attr]
        referenced_files=files,
        session_notes=str(data.get("session_notes", "")),
    )


def read_handoff(path: str) -> Handoff:
    with open(path, encoding="utf-8") as fh:
        return handoff_from_dict(parse_front_matter(fh.read()))


# ---------------------------------------------------------------------------
# Markdown body rendering
# ---------------------------------------------------------------------------

def render_body(h: Handoff) -> str:
    """Human-readable Markdown body for the handoff file."""
    out = [f"# Session handoff — {h.project}", ""]
    out.append(f"**{h.timestamp}** · id `{h.id}`")
    out.append("")
    out.append("## Session context")
    out.append("")
    out.append(h.summary or "_No summary recorded._")
    if h.session_notes:
        out.append("")
        out.append(h.session_notes)
    out.append("")
    out.append("## Decisions made")
    out.append("")
    out.extend(f"- {d}" for d in h.decisions) if h.decisions else out.append("- _None recorded._")
    out.append("")
    out.append("## Open tasks")
    out.append("")
    for t in h.open_tasks:
        mark = " " if t.is_open() else "x"
        out.append(f"- [{mark}] {t.text}")
    if not h.open_tasks:
        out.append("- _None recorded._")
    out.append("")
    out.append("## Constraints")
    out.append("")
    out.extend(f"- {c}" for c in h.constraints) if h.constraints else out.append("- _None recorded._")
    out.append("")
    out.append("## Key files")
    out.append("")
    for f in h.referenced_files:
        tag = " _(auto-detected)_" if f.auto_detected else ""
        note = f" — {f.note}" if f.note else ""
        out.append(f"- `{f.path}`{note}{tag}")
    if not h.referenced_files:
        out.append("- _None recorded._")
    out.append("")
    return "\n".join(out)


def render_prompt(h: Handoff) -> str:
    """Paste-ready context block for starting a new AI coding session."""
    lines = [
        "## CONTEXT HANDOFF",
        "",
        f"Project: {h.project}",
        f"Saved: {h.timestamp} (handoff `{h.id}`)",
        "",
        "### Session context",
        "",
        h.summary or "_No summary recorded._",
    ]
    if h.session_notes:
        lines += ["", h.session_notes]
    lines += ["", "### Decisions made", ""]
    lines += [f"- {d}" for d in h.decisions] or ["- _None recorded._"]
    lines += ["", "### Open tasks (pick these up first)", ""]
    open_only = [t for t in h.open_tasks if t.is_open()]
    lines += [f"- [ ] {t.text}" for t in open_only] or ["- _None — nothing pending._"]
    lines += ["", "### Constraints (do not violate)", ""]
    lines += [f"- {c}" for c in h.constraints] or ["- _None recorded._"]
    lines += ["", "### Key files to read first", ""]
    for f in h.referenced_files:
        note = f" — {f.note}" if f.note else ""
        lines.append(f"- `{f.path}`{note}")
    if not h.referenced_files:
        lines.append("- _None recorded._")
    lines += ["", "---",
              "Continue from the open tasks above. Respect every constraint. "
              "Ask before changing any decision listed."]
    return "\n".join(lines)


def render_short(h: Handoff) -> str:
    """One-paragraph recap."""
    bits = [f"{h.project} @ {h.timestamp}: {h.summary}".rstrip()]
    open_only = [t.text for t in h.open_tasks if t.is_open()]
    if open_only:
        bits.append(f"Open tasks: {len(open_only)} — next up: {open_only[0]}.")
    if h.decisions:
        bits.append(f"{len(h.decisions)} decision(s) recorded.")
    return " ".join(bits)


# ---------------------------------------------------------------------------
# IDs, slugs, git auto-detection
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "handoff")[:max_len].rstrip("-") or "handoff"


def now_timestamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def make_id(timestamp: str, summary: str) -> str:
    stamp = re.sub(r"[^0-9]", "", timestamp)[:14]  # YYYYMMDDHHMMSS
    return f"{stamp[:8]}-{stamp[8:]}-{slugify(summary)}"


def git_modified_files(root: str) -> List[str]:
    """Return paths with uncommitted changes via ``git status --porcelain``.

    Returns an empty list when ``root`` is not a git repo or git is missing.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    files: List[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        # handle renames: "R  old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in files:
            files.append(path)
    return files


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def init_project(root: str, project: Optional[str] = None) -> str:
    """Create ``.contextbridge/`` layout. Returns the project name used."""
    name = project or os.path.basename(os.path.abspath(root))
    cfg_dir = os.path.join(root, CONFIG_DIR)
    os.makedirs(os.path.join(cfg_dir, HANDOFFS_DIR), exist_ok=True)
    cfg_path = os.path.join(cfg_dir, CONFIG_FILE)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(f"# ContextBridge config\nproject: {name}\nhandoff_dir: {HANDOFFS_DIR}\n")
    return name


def save_handoff(root: str,
                 summary: str,
                 decisions: Optional[List[str]] = None,
                 tasks: Optional[List[str]] = None,
                 constraints: Optional[List[str]] = None,
                 files: Optional[List[ReferencedFile]] = None,
                 session_notes: str = "",
                 auto_detect: bool = True,
                 timestamp: Optional[str] = None) -> Handoff:
    """Record a handoff, refresh ``latest.md``, and return it."""
    cfg = read_config(root)
    ts = timestamp or now_timestamp()
    hid = make_id(ts, summary)
    ref_files = list(files or [])
    auto_paths: List[str] = []
    if auto_detect and not ref_files:
        auto_paths = [p for p in git_modified_files(root)
                      if not p.split("/", 1)[0] == CONFIG_DIR]
        ref_files = [ReferencedFile(path=p, note="uncommitted changes at save time",
                                    auto_detected=True) for p in auto_paths]
    h = Handoff(id=hid, timestamp=ts, project=cfg["project"], summary=summary,
                decisions=list(decisions or []),
                open_tasks=[Task(text=t) for t in (tasks or [])],
                constraints=list(constraints or []),
                referenced_files=ref_files,
                session_notes=session_notes)
    path = os.path.join(handoffs_dir(root), f"{hid}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dump_front_matter(h))
        fh.write(render_body(h))
    shutil.copyfile(path, os.path.join(root, CONFIG_DIR, LATEST_FILE))
    return h


def list_handoffs(root: str) -> List[Handoff]:
    """All handoffs, newest first (ids sort chronologically by construction)."""
    d = handoffs_dir(root)
    ids = sorted((f[:-3] for f in os.listdir(d) if f.endswith(".md")), reverse=True)
    return [read_handoff(os.path.join(d, f"{i}.md")) for i in ids]


def get_handoff(root: str, id_prefix: Optional[str] = None) -> Handoff:
    """Fetch the latest handoff, or the unique one matching an id prefix."""
    all_h = list_handoffs(root)
    if not all_h:
        raise HandoffError("no handoffs saved yet — run `cb save` first")
    if not id_prefix:
        return all_h[0]
    matches = [h for h in all_h if h.id.startswith(id_prefix)]
    if not matches:
        raise HandoffError(f"no handoff matches id prefix {id_prefix!r}")
    if len(matches) > 1:
        raise HandoffError(
            f"id prefix {id_prefix!r} is ambiguous: "
            + ", ".join(h.id for h in matches))
    return matches[0]


def _rewrite(root: str, h: Handoff) -> None:
    path = os.path.join(handoffs_dir(root), f"{h.id}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dump_front_matter(h))
        fh.write(render_body(h))
    latest = os.path.join(root, CONFIG_DIR, LATEST_FILE)
    if read_handoff(latest).id == h.id:
        shutil.copyfile(path, latest)


def append_decision(root: str, decision: str) -> Handoff:
    h = get_handoff(root)
    h.decisions.append(decision)
    _rewrite(root, h)
    return h


def complete_task(root: str, task_query: str) -> Handoff:
    """Mark the first open task matching ``task_query`` (case-insensitive) done."""
    h = get_handoff(root)
    needle = task_query.lower()
    for t in h.open_tasks:
        if t.is_open() and needle in t.text.lower():
            t.status = "done"
            _rewrite(root, h)
            return h
    raise HandoffError(f"no open task matches {task_query!r}")


def status_summary(root: str) -> Handoff:
    return get_handoff(root)
