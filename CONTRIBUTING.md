# Contributing

Contributions are welcome. Open an issue first if the change is large enough that you
would be annoyed to have it rejected; otherwise a pull request is fine.

By submitting a contribution you agree that it lands under this repository's license,
[PolyForm Noncommercial 1.0.0](LICENSE.md), on the same terms as the rest of the code.

## Before you open a PR

Read [`AGENTS.md`](AGENTS.md) — it states the principles this codebase is unwilling to
trade away, and it is the fastest way to understand why a change might be rejected on
grounds that have nothing to do with whether it works. [`docs/code-tour.md`](docs/code-tour.md)
is the guided read of the layers.

Then run the verification bar from `AGENTS.md`, from the repo root:

```bash
uv run pytest -q -p no:randomly        # full backend suite
uv run pytest -m reference             # reference conversations vs answer key
uv run pytest -m postgres              # store parity (needs make db-up migrate)
uv run ruff check . && uv run lint-imports
make warehouse-diff                    # independent rederivation, zero divergence
cd apps/web && pnpm test && pnpm lint && pnpm build
```

A PR that has not run the bar is a PR that has not been tested. Say so in the
description if you could not run part of it, and why — an honest gap is workable, a
silent one is not.

## What gets pushed back

- New numbers that no versioned metric contract produces.
- Domain semantics hard-coded into application code instead of the pack.
- Tests that assert on the shape of an answer rather than its value.
- Anything that makes the system state a figure it cannot show provenance for.
