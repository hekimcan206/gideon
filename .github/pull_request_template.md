## What and why

<!-- One or two sentences: the problem this change solves. Link the issue if there is one. -->

## How

<!-- The approach, and alternatives you rejected if they are not obvious. -->

## Checklist

- [ ] Backend: `ruff check`, `ruff format --check`, `mypy` and `pytest` pass (`pytest -m engines` too if a pipeline stage changed)
- [ ] UI: `npm run typecheck`, `npm test` and `npm run build` pass
- [ ] Protocol change: `python -m gideon export-schema` + `npm run gen:protocol` committed; version bumped if not backwards compatible
- [ ] Latency-relevant change: before/after numbers from `python -m gideon bench` in the description
- [ ] A new ADR in `docs/adr/` if this changes an architectural decision
- [ ] No secrets, no recordings of real people
