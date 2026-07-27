# Contributing to fast-pager

Thanks for your interest! This project aims to be small, sharp, and
pleasant to work on — the workflow below keeps it that way.

## Development setup

Requirements: [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11.

```bash
git clone https://github.com/eytanohana/fast-pager
cd fast-pager
uv sync --all-groups          # runtime + dev + docs dependencies
uv run pre-commit install     # optional: run the CI gates before each commit
```

## Quality gates

Everything CI enforces, runnable locally (all must pass before a PR):

```bash
uv run ruff check                          # lint
uv run ruff format --diff                  # formatting
uv run mypy                                # strict type-checking on src/
uv run pytest tests                        # tests; coverage floor enforced
uv run zensical build --clean --strict     # docs build (when touching docs/)
```

CI also runs the test matrix over Python 3.11–3.14 and a
lowest-supported-dependencies job that proves the declared `fastapi`/
`pydantic` floors.

## How the project is organized

- `src/fast_pager/` — the library. The core (introspection → params → AST)
  imports no database code; backends live in `fast_pager.backends.*`.
- `docs/design/` — the product design. **The design docs are authoritative**:
  if an implementation should diverge, improve the design doc first, then
  conform to it (see `DEVELOPMENT_PLAN.md` for the full standards).
- `DEVELOPMENT_PLAN.md` — the staged execution plan and version map.
- `examples/` — runnable example apps, exercised by the test suite.
- `docs/` — the [Zensical](https://zensical.org) documentation site;
  see `docs/contributing/docs-site.md` for preview and deployment.

## Conventions

- **Commits:** conventional commits (`feat:`, `fix:`, `docs:`, `chore:`),
  small and reviewable — one logical unit per commit.
- **Tests:** mirror the package layout under `tests/`; new behavior ships
  with tests (the suite currently sits at 100% coverage — please keep it
  high).
- **Errors:** misconfiguration fails at route registration with a message
  naming the field/operator and the valid alternatives — never a 500 at
  request time.
- **Docs:** user-facing changes update the relevant tutorial/reference page
  and `docs/changelog.md` in the same PR.

## Releases

Maintainers only — see the "Releasing" section of the README. Versioning
follows the plan's version map (pre-1.0 SemVer: breaking changes bump the
minor version and are called out in the changelog).

## Built with AI

This project is developed with AI assistance under human review (see the
README). Contributions are held to the same standards regardless of how
they were written.
