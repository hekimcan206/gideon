# Contributing

## Prerequisites

- Python 3.12 or 3.13, Node.js 22.12+ (CI uses 22), Docker for the compose demo.
- No local Python? `docker build --target dev -t gideon-backend:dev backend`, then run any backend command through
  `scripts/dev-docker.sh` (for example `scripts/dev-docker.sh pytest -q`).

```bash
scripts/install.sh --dev          # Windows: scripts\install.ps1 -Dev
```

## Before pushing

```bash
cd backend
ruff check src tests && ruff format --check src tests
mypy                                        # strict
pytest -m "not engines"                     # unit tests with fakes (seconds)
python -m gideon fixtures && pytest -m engines   # real engines on synthetic WAVs (about a minute)

cd ../ui
npm run typecheck && npm test && npm run build
```

## Changing the protocol

1. Edit the pydantic models in `backend/src/gideon/protocol.py`.
2. `python -m gideon export-schema` (writes `protocol/schema.json`), then `npm run gen:protocol` in `ui/`.
3. Commit both generated files. CI fails if either is stale.
4. Removing, renaming or redefining a field bumps `PROTOCOL_VERSION` ([ADR 0001](docs/adr/0001-python-backend-electron-ui-over-websocket.md)).

## Adding a skill

A skill is any object with `name`, `intents` (a sequence of `IntentSpec` with regex patterns **and example
phrases** per language) and `async handle(match, ctx) -> SkillResponse`. Register it in `gideon/app.py::build_router`.
The order matters where phrasings overlap. Add golden cases to `tests/test_skills.py` for both languages,
including the way faster-whisper actually spells things (missing diacritics, split words). The example phrases
become part of the STT vocabulary prompt.

Skills that act on the system must take their parameters from a fixed allow-list, never from transcript text
([threat model](docs/threat-model.md) T4).

## Latency-relevant changes

Run `python -m gideon bench --runs 10` before and after, and put both summaries in the pull request.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/). User-visible changes (`feat:`, `fix:`, `perf:`)
also get a line in `CHANGELOG.md` (Keep a Changelog format, maintained by hand).

## Architecture decisions

A change to a decision in `docs/adr/` needs a new ADR that supersedes the old one (MADR format).
