"""Command-line interface for ContextBridge (the ``cb`` command)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import core
from .core import HandoffError, ReferencedFile


def _root() -> str:
    try:
        return core.find_root()
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    name = core.init_project(os.getcwd(), args.project)
    print(f"initialized ContextBridge for project {name!r} in .contextbridge/")
    return 0


def _prompt_lines(prompt: str) -> list[str]:
    print(f"{prompt} (blank line to finish):")
    lines: list[str] = []
    while True:
        try:
            line = input("  > ").strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return lines


def cmd_save(args: argparse.Namespace) -> int:
    root = _root()
    if args.summary:
        summary = args.summary
        decisions = args.decision or []
        tasks = args.task or []
        constraints = args.constraint or []
        files = [ReferencedFile(path=p) for p in (args.file or [])]
        notes = args.notes or ""
    else:
        print("Recording a handoff — describe where this session stands.\n")
        try:
            summary = input("Summary (one line): ").strip()
        except EOFError:
            summary = ""
        if not summary:
            print("cb: error: a summary is required", file=sys.stderr)
            return 1
        decisions = _prompt_lines("Decisions made")
        tasks = _prompt_lines("Open tasks")
        constraints = _prompt_lines("Architectural constraints")
        file_paths = _prompt_lines("Referenced files (paths)")
        files = [ReferencedFile(path=p) for p in file_paths]
        try:
            notes = input("Session notes (optional): ").strip()
        except EOFError:
            notes = ""
    auto = not (args.summary and args.file)
    h = core.save_handoff(root, summary,
                          decisions=decisions, tasks=tasks,
                          constraints=constraints, files=files or None,
                          session_notes=notes, auto_detect=auto)
    detected = sum(1 for f in h.referenced_files if f.auto_detected)
    extra = f" ({detected} auto-detected from git)" if detected else ""
    print(f"saved handoff {h.id}{extra}")
    print(f"  {len(h.decisions)} decision(s), "
          f"{len(h.open_tasks)} open task(s), "
          f"{len(h.constraints)} constraint(s), "
          f"{len(h.referenced_files)} file(s)")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    root = _root()
    try:
        h = core.get_handoff(root, args.id)
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({
            "id": h.id, "timestamp": h.timestamp, "project": h.project,
            "summary": h.summary, "session_notes": h.session_notes,
            "decisions": h.decisions,
            "open_tasks": [{"text": t.text, "status": t.status} for t in h.open_tasks],
            "constraints": h.constraints,
            "referenced_files": [{"path": f.path, "note": f.note,
                                  "auto_detected": f.auto_detected}
                                 for f in h.referenced_files],
        }, indent=2))
    elif args.format == "short":
        print(core.render_short(h))
    else:
        print(core.render_prompt(h))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = _root()
    handoffs = core.list_handoffs(root)
    if not handoffs:
        print("no handoffs saved yet — run `cb save` first")
        return 0
    for h in handoffs:
        open_n = sum(1 for t in h.open_tasks if t.is_open())
        done_n = len(h.open_tasks) - open_n
        print(f"{h.id}\n  {h.timestamp} · {h.summary}\n"
              f"  tasks: {open_n} open / {done_n} done · "
              f"decisions: {len(h.decisions)}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    root = _root()
    try:
        h = core.complete_task(root, args.task)
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        return 1
    remaining = sum(1 for t in h.open_tasks if t.is_open())
    print(f"marked done in handoff {h.id} — {remaining} open task(s) left")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    root = _root()
    try:
        h = core.append_decision(root, args.decision)
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        return 1
    print(f"recorded decision in handoff {h.id} "
          f"({len(h.decisions)} decision(s) total)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = _root()
    try:
        h = core.status_summary(root)
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        return 1
    open_tasks = [t.text for t in h.open_tasks if t.is_open()]
    print(f"project: {h.project}")
    print(f"latest:  {h.id} ({h.timestamp})")
    print(f"summary: {h.summary}")
    print(f"decisions: {len(h.decisions)} · "
          f"open tasks: {len(open_tasks)} · "
          f"constraints: {len(h.constraints)} · "
          f"files: {len(h.referenced_files)}")
    for t in open_tasks:
        print(f"  [ ] {t}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cb",
        description="ContextBridge — structured handoffs between AI coding sessions.")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="initialize .contextbridge/ in this project")
    i.add_argument("--project", help="project name (default: directory name)")
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("save", help="record a handoff (interactive if no flags)")
    s.add_argument("--summary", help="one-line session summary")
    s.add_argument("--decision", action="append", dest="decision",
                   help="a decision made (repeatable)")
    s.add_argument("--task", action="append",
                   help="an unfinished task (repeatable)")
    s.add_argument("--constraint", action="append",
                   help="an architectural constraint (repeatable)")
    s.add_argument("--file", action="append",
                   help="a referenced file path (repeatable)")
    s.add_argument("--notes", default="", help="free-form session notes")
    s.set_defaults(func=cmd_save)

    l = sub.add_parser("load", help="print a handoff as a context block")
    l.add_argument("id", nargs="?", help="handoff id prefix (default: latest)")
    l.add_argument("--format", choices=["prompt", "json", "short"],
                   default="prompt", help="output format (default: prompt)")
    l.set_defaults(func=cmd_load)

    ls = sub.add_parser("list", help="list saved handoffs, newest first")
    ls.set_defaults(func=cmd_list)

    d = sub.add_parser("done", help="mark an open task done in the latest handoff")
    d.add_argument("task", help="text (or unique substring) of the task")
    d.set_defaults(func=cmd_done)

    dc = sub.add_parser("decide", help="append a decision to the latest handoff")
    dc.add_argument("decision", help="the decision to record")
    dc.set_defaults(func=cmd_decide)

    st = sub.add_parser("status", help="show latest handoff summary and counts")
    st.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except HandoffError as exc:
        print(f"cb: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
