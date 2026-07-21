# 05 — Roadmap & Release Plan

A phased path from empty repo to a clean, documented, pleasurable 1.0 on PyPI.
Each phase ends with something shippable and is gated by concrete exit criteria.

## Project foundations (do this once, in Phase 0)

- **Tooling:** `uv` for env/deps, `hatchling` build backend, `ruff`
  (lint + format), `mypy --strict`, `pytest` + `pytest-cov`.
- **Layout:** `src/fast_pager/` package layout; `tests/`; `docs/` (MkDocs
  Material); `examples/` (runnable FastAPI apps).
- **Quality gates in CI** (GitHub Actions): lint, type-check, test matrix over
  Python 3.11–3.14 and Pydantic v2.x. Coverage threshold. (3.10 is skipped:
  it reaches end-of-life in October 2026, around when this ships.) The FastAPI
  floor is decided by the Phase 1 parameter-generation spike (doc 03) — the
  native query-model path requires **FastAPI ≥ 0.115**.
- **Pre-commit** hooks mirroring CI. **Conventional commits** + automated
  changelog. **Semantic versioning**, `0.x` until the API stabilizes.
- **Docs site from day one** — design docs (these files) seed it. Docs are a
  feature, not an afterthought.
- **License:** **MIT** (permissive → maximize adoption; matches FastAPI,
  Pydantic, and the ecosystem's expectations). Decided — add `LICENSE` in
  Phase 0.

Dependencies kept minimal: `fastapi`, `pydantic>=2`, `typing-extensions`. Drivers
are optional extras (`[mongo]`, `[sqlalchemy]`). Core has **no database
dependency**.

---

## Phase 1 — Core + Mongo, scalars only (`0.1`)

**Goal:** the headline demo works end to end for scalar fields.

- Introspector for scalar + `Optional` fields.
- Operator registry with `safe`/`full` tiers for `str`, numerics, `bool`,
  datetimes, `UUID`, `Enum`/`Literal`.
- Parameter generation spike (native query-model vs synthesized signature,
  doc 03), then implementation → params visible & validated in `/docs`.
- `FilterAST` + `MongoCompiler` (returns plain dict).
- `FilterDepends(Model)` + `FilterQuery[Model]` (Option C zero-config).
- Pagination (`offset`/`limit`) + sorting (`sort=`), with `max_limit` guard.
- Safety defaults: `regex` off, list caps, sortable allow-list.

**Exit criteria:** the README example runs; `to_mongo()`/`sort_mongo()`/`skip`/
`limit` correct; OpenAPI shows typed params; >90% coverage on core; mypy-strict
clean.

---

## Phase 2 — Configurability + compound types (`0.2`–`0.3`)

**Goal:** real-world models, real control.

- `Annotated[T, Filterable(...)]` (Option A): per-field ops, `source`, `param`.
- `FilterSet` class (Option B) with allow-list `fields` + custom filters.
- Compound types: `list[scalar]` (`has`/`has_any`/`has_all`/`len`/`empty`),
  nested models (dotted paths, depth-bounded), `dict` (gated).
- `list[NestedModel]` via `elem`/`$elemMatch` (likely `full`-tier).
- `strict` mode for unknown params; rich config-time error messages.

**Exit criteria:** all type tables in doc 02 implemented & tested; a non-trivial
example app (users + addresses + tags + orders) filters correctly.

---

## Phase 3 — Second backend + ergonomics (`0.4`–`0.6`)

**Goal:** prove the multi-backend thesis; polish DX.

- **SQLAlchemy adapter** + capability model + the **conformance test suite**.
- `Page[T]` response envelope + `q.paginate(...)` helpers (per-backend).
- Cursor/keyset pagination design implemented for at least one backend.
- Adapter authoring guide published.

**Exit criteria:** the *same* example endpoints run on Mongo and SQLAlchemy with
only a backend swap; conformance suite passes for both.

---

## Phase 4 — Hardening → `1.0`

**Goal:** something you'd happily put in production and recommend.

- API freeze; deprecation policy documented.
- Performance pass: param-generation memoization, query-merge correctness,
  benchmarks published.
- Security pass: ReDoS guards, injection review (Mongo operator-injection via
  values, SQL via parameter binding), fuzz tests on the parser.
- Docs: tutorial, recipes, migration-from-hand-rolled guide, full API reference,
  every operator with a live example.
- `examples/` apps for Mongo and SQL; a short screencast.

**Exit criteria:** stable public API, comprehensive docs, two backends, green
conformance suite, real-world adopter feedback incorporated.

---

## Post-1.0 ideas (not commitments)

- Elasticsearch/OpenSearch adapter; Beanie/ODMantic native expressions.
- Opt-in OR-group syntax (or a `POST /search` JSON-filter companion).
- Saved/named filters; field-level RBAC (hide filters per caller role).
- A `pytest` plugin offering fixtures for asserting on generated ASTs.
- Codegen of typed client query builders from the OpenAPI surface.

---

## What makes the 1.0 *pleasant* (the bar we hold)

1. **One-line happy path.** `FilterQuery[Model]` and you're filtering.
2. **No drift.** Params, docs, and validation all derive from the model.
3. **No surprises.** Safe defaults; loud, early, specific errors.
4. **Inspectable.** Plain `FilterAST` and plain query objects — testable and
   loggable, no magic.
5. **Honest about limits.** AND-only in v1, capability-aware backends, clear
   non-goals. We'd rather do a sharp thing well than a fuzzy thing everywhere.

Back to **[00 — Overview](00-overview.md)**.
