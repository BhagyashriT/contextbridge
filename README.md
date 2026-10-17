# ContextBridge

**Structured handoffs between AI coding sessions.** When a coding session ends,
run `cb save` to record the decisions you made, the tasks still open, the
architectural constraints, and the files that matter. When the next session
starts — hours later, a different day, a different AI — run `cb load` and paste
the context block back in. No more re-explaining the project from scratch.

Stdlib-only Python 3.9+. No dependencies, no network, no accounts.

## The problem

Every AI coding session starts at zero. You spend the first twenty minutes
re-explaining the architecture, restating decisions ("we chose X because Y"),
and re-listing what's left to do — and the AI still occasionally violates a
constraint you settled three sessions ago. Context lives in the chat history,
and chat history doesn't survive the session boundary.

ContextBridge makes the session boundary explicit: a small, versioned,
human-readable handoff file that captures exactly what the next session needs.

## Install

```bash
git clone <your-fork> contextbridge
cd contextbridge
pip install -e .
```

This installs the `cb` command. Verify with `cb --help`… (well, `cb` with no
subcommand prints help).

Run the test suite:

```bash
python -m unittest discover -s tests -v
```

## Quickstart

```bash
cd ~/my-project
cb init --project my-project
```

End of a session — record the handoff (interactive prompts if you pass no flags):

```bash
cb save \
  --summary "wired up JWT auth middleware; login works end to end" \
  --decision "JWT access tokens (15m) + rotating refresh tokens; no server sessions" \
  --decision "rate limit login at 10/min per IP via existing limiter" \
  --task "add refresh-token rotation endpoint" \
  --task "write login + refresh integration tests" \
  --constraint "stdlib + FastAPI only — no new dependencies" \
  --constraint "never log raw tokens, only key IDs" \
  --file app/auth.py \
  --file app/routes/login.py
```

Omitted `--file` flags? `cb save` auto-detects your uncommitted git changes and
records them as referenced files, marked as auto-detected.

Between sessions, keep the latest handoff fresh without a full re-save:

```bash
cb done "write login"          # mark an open task done (substring match)
cb decide "refresh tokens live 7 days"   # append a decision
cb status                      # latest summary + open/decision counts
```

Next session starts — get your context back:

```bash
cb load
```

```
## CONTEXT HANDOFF

Project: my-project
Saved: 2026-10-16T18:42:10-07:00 (handoff `20261016-184210-wired-up-jwt-auth-middleware`)

### Session context

wired up JWT auth middleware; login works end to end

### Decisions made

- JWT access tokens (15m) + rotating refresh tokens; no server sessions
- rate limit login at 10/min per IP via existing limiter

### Open tasks (pick these up first)

- [ ] add refresh-token rotation endpoint
- [ ] write login + refresh integration tests

### Constraints (do not violate)

- stdlib + FastAPI only — no new dependencies
- never log raw tokens, only key IDs

### Key files to read first

- `app/auth.py`
- `app/routes/login.py`

---
Continue from the open tasks above. Respect every constraint. Ask before changing any decision listed.
```

Paste that block into the new chat and you're productive in seconds.

## Command reference

| Command | What it does |
|---|---|
| `cb init [--project NAME]` | create `.contextbridge/` in the current project |
| `cb save [flags]` | record a handoff; interactive prompts when no flags given |
| `cb load [<id-prefix>] [--format prompt\|json\|short]` | print a handoff (default: latest, paste-ready prompt block) |
| `cb list` | list handoffs newest-first with task/decision counts |
| `cb done <task>` | mark an open task done in the latest handoff (substring match) |
| `cb decide <decision>` | append a decision to the latest handoff |
| `cb status` | latest handoff summary + counts of open tasks/decisions |

`cb save` flags (all repeatable except `--summary`/`--notes`):

`--summary`, `--decision`, `--task`, `--constraint`, `--file`, `--notes`

`cb load --format json` emits the raw handoff data for scripting; `--format short`
gives a one-paragraph recap.

## Handoff file format

Each handoff is `.contextbridge/handoffs/<YYYYMMDD-HHMMSS-slug>.md`: a YAML
front-matter block (machine-readable) followed by a Markdown body
(human-readable). `latest.md` is always a copy of the most recent handoff.

```yaml
---
id: "20261016-184210-wired-up-jwt-auth-middleware"
timestamp: "2026-10-16T18:42:10-07:00"
project: "my-project"
summary: "wired up JWT auth middleware; login works end to end"
session_notes: ""
decisions:
  - "JWT access tokens (15m) + rotating refresh tokens; no server sessions"
constraints:
  - "stdlib + FastAPI only — no new dependencies"
open_tasks:
  - text: "add refresh-token rotation endpoint"
    status: open
referenced_files:
  - path: "app/auth.py"
    note: ""
    auto_detected: false
---
```

Schema fields: `id`, `timestamp`, `project`, `summary`, `decisions[]`,
`open_tasks[]` (`text`, `status`), `constraints[]`, `referenced_files[]`
(`path`, `note`, `auto_detected`), `session_notes`. The schema is append-only —
new fields must never break old readers.

A real example handoff (this project dogfooding itself) lives at
[`examples/sample-handoff.md`](examples/sample-handoff.md).

## Design notes

- **Stdlib only** — argparse, dataclasses, subprocess. `pip install -e .` just works.
- **Files, not a database** — handoffs are plain Markdown, diffable and greppable.
- **Git-aware, not git-dependent** — auto-detection degrades silently outside repos.

## Roadmap

- [ ] VS Code extension: `Cmd+Shift+H` to save, auto-load prompt on workspace open
- [ ] Git hooks: suggest `cb save` on branch switch / pre-commit with dirty tree
- [ ] MCP server: expose handoffs as context resources to MCP-capable AI clients
- [ ] `cb edit`: reopen the latest handoff in `$EDITOR`
- [ ] `cb search`: full-text search across handoffs
- [ ] PyPI release (`pip install contextbridge`)

## License

MIT — see [LICENSE](LICENSE).
