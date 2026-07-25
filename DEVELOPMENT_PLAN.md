# fast-pager — Development Plan

This is the execution plan for implementing `fast-pager`, derived from the
design documents in [`docs/design/`](docs/design/). Where this plan and the
design docs disagree, the design docs win — update them first, then this plan.

Each stage ends in a **published release** (via `./scripts/release.sh`, which
tags `vX.Y.Z` and triggers the automated PyPI + GitHub Release flow). Releases
are never cut without maintainer approval. `0.x` versions signal an unstable
API per SemVer; breaking changes bump the minor version and are called out in
release notes.

**Documentation is developed alongside code in every stage** — a stage is not
done until its docs pages exist on the Zensical site. The docs site itself is a
Stage 1 deliverable.

---

## Standards (apply to every stage)

- **Layout:** `src/fast_pager/` package, `tests/` mirroring the package
  structure, `docs/` as the Zensical site source.
- **Quality gates (CI-enforced):** `ruff check` + `ruff format --diff`,
  `mypy --strict` on `src/`, `pytest` with coverage ≥ 90% on `src/fast_pager/`,
  across Python 3.11–3.14.
- **Design invariants:** the core (introspection → params → AST) imports no
  database code; backend adapters live in `fast_pager.backends.*`; every
  config error surfaces at route registration, never as a runtime 500; safety
  defaults per design doc 02.
- **Commits:** conventional commits (`feat:`, `fix:`, `docs:`, `chore:`);
  small and reviewable.
- **Docs:** every public symbol has a docstring; every operator documented
  with a request → compiled-query example.
- **Releases:** `./scripts/release.sh patch|minor|major` from a clean `main`
  with green CI. Maintainer approval required before any tag is pushed.

---

## Version map (summary)

| Version | Stage | Headline |
|---|---|---|
| `0.0.1` | 0 | ✅ Name reserved on PyPI; CI + release automation |
| `0.0.2` | 1 | ✅ Stage 1 in full (both planned checkpoints landed together): core engine + FastAPI integration + versioned docs site — the README demo works |
| `0.0.3` | 2 | Per-field control: `Annotated[T, Filterable(...)]`, per-type profiles, strict mode |
| `0.1.0` | 1–2 | Stages 1–2 exit criteria green, docs describe the shipped API, polish |
| `0.2.0` | 3 | `FilterSet` + compound types (arrays, nested models, `elem`) |
| `0.3.0` | 4 | `Page[T]` envelope, `paginate()` helpers, count modes |
| `0.4.0` | 5 | SQLAlchemy backend + conformance test suite |
| `0.5.0` | 6 | Cursor/keyset pagination + adapter authoring guide |
| `1.0.0` | 7 | API freeze, security/perf hardening, complete docs |

Patch releases (`0.x.y`) ship bug fixes between stages as needed. Larger
stages may add intermittent checkpoint releases when a coherent, working
slice lands early — decided per-stage with maintainer approval like any
release. (Stage 1 planned two checkpoints but shipped as one, `0.0.2`;
Stage 2 ships as the `0.0.3` checkpoint and is finalized in `0.1.0` — the
stage→version rows above reflect the renumbering.)

**Commit discipline:** work lands as small, human-reviewable commits — one
logical unit per commit (a module with its tests, a workflow, a docs
section), conventional-commit messages. Bulk "implement everything" commits
are not acceptable, including when the work was produced by an AI agent.

---

## Stage 0 — Foundations *(✅ done, released as `v0.0.1`)*

Placeholder package reserving the PyPI name; `uv_build` packaging; CI (ruff,
pytest matrix 3.11–3.14); tag-triggered release workflow with tag↔version
guard and PyPI Trusted Publishing; `scripts/release.sh`.

---

## Stage 1 — Core pipeline + Mongo, scalars only → `v0.1.0`

**Goal:** the README example works end to end for scalar fields, and the
project has a live documentation site.

### 1a. Package skeleton & typing

1. Add runtime dependencies: `fastapi>=0.115`, `pydantic>=2.7`.
2. Module layout:
   ```
   src/fast_pager/
       __init__.py          # public API re-exports
       config.py            # FilterConfig (profiles, limits, separator)
       introspection.py     # model → FieldSpec tree
       operators.py         # Operator registry, safe/full tiers
       params.py            # FieldSpec × operators → dynamic Pydantic query model
       ast.py               # Condition / Group / Sort / Page / FilterAST
       query.py             # FilterQuery[Model]: parsed result object
       dependency.py        # FilterDepends(...)
       errors.py            # ConfigurationError, etc.
       py.typed
       backends/
           __init__.py
           base.py          # QueryCompiler protocol + capability declaration
           mongo.py         # MongoCompiler → plain dict (no driver dependency)
   ```
3. Add `mypy --strict` to CI and pre-commit-ready config in `pyproject.toml`.

### 1b. Implementation steps (in dependency order)

1. **`ast.py`** — frozen dataclasses `Condition`, `Group`, `Sort`, `Page`,
   `FilterAST` (design doc 03). Pure data, fully typed, no imports from the
   rest of the package.
2. **`operators.py`** — `Operator` records (name, arity, value-type rule,
   applicable containers, tier) and the default registry for scalar types:
   `str`, `int`/`float`/`Decimal`, `bool`, `datetime`/`date`/`time`, `UUID`,
   `Enum`/`Literal` per the doc 02 tables. `safe`/`full` tiers; `regex` gated.
3. **`introspection.py`** — walk Pydantic v2 `model_fields` into `FieldSpec`
   (path, source name, unwrapped type, container kind, nullability). Scalars +
   `Optional` only in this stage; respect Pydantic aliases.
4. **`params.py`** — generate the parameter set from `FieldSpec × profile`
   (exact-name matching, no request-time parsing — doc 02), then build the
   dynamic Pydantic query model via `create_model()` with `Query()` metadata
   (doc 03). **Spike first:** validate the FastAPI ≥ 0.115 native query-model
   path (aliases containing `__`, list params, OpenAPI output); fall back to
   signature synthesis only if it fails. Record the outcome in
   `docs/design/03-architecture.md`. Memoize per (model, config).
5. **`query.py` + `dependency.py`** — `FilterDepends(Model)` returning a
   `FilterQuery[Model]` with `.to_ast()`, `.to_mongo()`, `.sort_mongo()`,
   `.skip`, `.limit`, `.applied`. Pagination params (`limit`/`offset`, with
   `default_limit`/`max_limit`) and `sort=` parsing with the sortable
   allow-list.
6. **`backends/mongo.py`** — compile the AST to a plain dict: operator
   mapping, same-field condition merging, `re.escape()` for `contains`-family,
   anchored `startswith`/`endswith`. No pymongo/motor dependency.
7. **Safety defaults wired end to end:** `regex` off, `max_list_length`,
   `max_filters`, `max_limit`, sortable allow-list (doc 02).

### 1c. Tests (target ≥ 90% coverage)

- Unit: introspection (each scalar type, Optional, aliases), operator
  resolution per profile, AST construction, Mongo compilation table
  (parametrized: every operator → expected dict), same-field merging,
  escaping.
- Integration: a real FastAPI app via `TestClient` — params appear in
  `/openapi.json` with correct types; good requests produce the expected
  Mongo dict; bad values return 422; unknown params honor `ignore` mode;
  limits enforced.
- Config-time failure tests: invalid operator for type raises
  `ConfigurationError` at registration with field+operator in the message.

### 1d. Docs site (Zensical) — built in parallel with 1b

1. Set up **Zensical** (`docs` dependency group) with `docs/` as source:
   landing page modeled on FastAPI's docs style (hero example, feature
   bullets), Getting Started, Tutorial (filtering, sorting, pagination),
   Operator Reference (generated tables from doc 02), Design section
   (the existing `docs/design/*.md` files in nav), Changelog.
2. **Document the docs site itself**: a `docs/contributing/docs-site.md` page
   covering how Zensical is configured, how to preview locally
   (`uv run zensical serve` or equivalent), how deploys work, and how to add
   pages/nav entries.
3. CI: `docs.yml` workflow — build the site on PRs (link/error check), deploy
   to GitHub Pages on push to `main`.
4. README gets the docs-site link + badges (PyPI version, CI, docs).

### Exit criteria → release `v0.1.0` *(with maintainer approval)*

README example runs against the real package; OpenAPI shows typed params;
coverage ≥ 90%; `mypy --strict` clean; docs site deployed and linked; version
bumped by `release.sh minor`.

---

## Stage 2 — Per-field control (Option A) → `v0.0.3`, finalized in `v0.1.0`

1. `Filterable(ops=..., source=..., param=...)` metadata read from
   `Annotated[T, Filterable(...)]` (doc 01 Option A; doc 02 layering rules:
   field-level beats type-level beats global).
2. `FilterConfig(type_profiles={...})` per-type overrides and global
   `exclude=[...]`.
3. `strict` unknown-param mode (422 on unrecognized `field__op`).
4. Rich `ConfigurationError` messages (field, operator, valid alternatives).
5. Docs: "Controlling the filter surface" tutorial page; config reference.
6. Exit: layering precedence fully tested; docs updated → release `v0.0.3`.

## Stage 3 — FilterSet + compound types → `v0.2.0`

1. `FilterSet` class (doc 01 Option B): `Meta.model`, allow-list `fields`
   mapping, custom declared filters; multiple filtersets per model.
2. Compound types (doc 02): `list[scalar]` (`has`, `has_any`, `has_all`,
   `len__*`, `empty` with the pinned empty-vs-missing semantics), nested
   models via dotted source paths (depth-bounded, cycle-safe), `Optional`
   `isnull`/`exists`, `dict` with enumerated keys (gated), `list[Nested]`
   via `elem` → `$elemMatch` (full tier).
3. Docs: FilterSet guide; compound-types guide; array-semantics page
   (the `$elemMatch` surprise, documented loudly).
4. Exit: all doc 02 type tables implemented & tested; the non-trivial example
   app (users + addresses + tags + orders) works → release `v0.3.0`.

## Stage 4 — Response envelope & ergonomics → `v0.3.0`

1. `Page[T]` generic model; `q.paginate(collection, total="exact|estimated|none")`
   (doc 01): optional `[mongo]` extra for motor/pymongo conveniences.
2. `page`/`page_size` strategy (sugar over offset).
3. Docs: pagination guide incl. count-cost tradeoffs.
4. Exit: envelope + OpenAPI schema correct; release `v0.3.0`.

## Stage 5 — SQLAlchemy backend + conformance suite → `v0.4.0`

1. **Conformance test suite first** (doc 04): a fixed battery of
   `FilterAST → expected shape` cases every adapter must pass; run it against
   `MongoCompiler` to lock in behavior.
2. `backends/sqlalchemy.py`: Condition → `ColumnElement` expressions,
   capability declaration (which ops it supports), `[sqlalchemy]` extra;
   flat tables + JSON/JSONB first, relationship JOINs deferred.
3. Docs: backend selection guide; capability matrix page.
4. Exit: the same example endpoints run on Mongo and SQLAlchemy with only a
   backend swap; both pass conformance → release `v0.4.0`.

## Stage 6 — Cursor pagination + adapter guide → `v0.5.0`

1. Keyset pagination with opaque cursor tokens; automatic unique tiebreaker
   (`_id` / PK) appended to the sort key (doc 01).
2. Published **adapter authoring guide** + conformance-suite usage for
   third-party backends.
3. Docs: cursor pagination guide; adapter guide.
4. Exit: cursor strategy on at least one backend, conformance-tested →
   release `v0.5.0`.

## Stage 7 — Hardening → `v1.0.0`

1. API freeze + deprecation policy; `__all__` audit.
2. Security pass: ReDoS guards, operator-injection review, parser fuzzing.
3. Performance pass: generation memoization benchmarks, published numbers.
4. Docs completeness: every operator with a live example; migration guide
   from hand-rolled filtering; API reference generated from docstrings.
5. Exit: two backends green on conformance, docs complete, real-world
   feedback incorporated → release `v1.0.0`.

---

## Working agreements for AI-assisted development

This project is built with AI agents doing implementation under human review
(see README). Agents must:

- follow this plan and the design docs; propose doc changes rather than
  silently diverging;
- keep the core backend-free and the safety defaults intact;
- never publish: no pushing of `v*` tags, no `release.sh`, no `uv publish` —
  releases require explicit maintainer approval;
- leave the tree green: ruff, mypy, pytest all passing before handing back.
