---
icon: lucide/history
---

# Changelog

`fast-pager` follows [SemVer](https://semver.org/); `0.x` versions signal an
unstable API, and breaking changes bump the minor version and are called out
below. See the [Roadmap](design/05-roadmap-and-release.md) for what's coming
next, and the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)
for the execution detail behind each stage.

## `0.3.0` — Stage 4: response envelope & ergonomics

The "list + total count" shape is now one line. **`Page[T]`** is a generic
Pydantic response envelope (`items` / `total` / `limit` / `offset`), so
`response_model=Page[User]` keeps response validation and the OpenAPI
schema exactly right, and **`await q.paginate(collection)`** runs the
compiled find (+ optional count) and fills it in. The collection is
**duck-typed** against the standard Mongo surface (`find` → cursor with
`sort`/`skip`/`limit` iterated via `to_list`/async/plain iteration,
`count_documents`, `estimated_document_count`) with awaitables detected at
runtime — motor, pymongo sync, pymongo async, and in-memory test fakes all
work, and the core still imports **no driver** (a `[mongo]` extra exists
purely as an install convenience). The count cost is a knob:
`total="exact"` (default) counts with the same filter, `"none"` skips
counting (`Page.total` is `None`), and `"estimated"` uses the metadata-based
`estimated_document_count()` **only for unfiltered queries** — a filtered
query falls back to an exact count per the design doc's pinned rule, so
`total` is never a number unrelated to the results. Rounding out the
ergonomics, `FilterConfig(pagination="page")` switches a route's generated
parameters to a 1-based **`page`** + **`page_size`** pair (same
`default_limit`/`max_limit` guardrails) that resolve to the identical
internal window — `q.limit`/`q.offset`/`q.skip`, backends, and `paginate()`
are strategy-agnostic.

**One breaking change** (0.x minor bump per SemVer): the internal AST
pagination-window dataclass previously exported as `fast_pager.Page` /
`fast_pager.ast.Page` is renamed **`PageSpec`** — the public `Page` name
now means the response envelope, matching design doc 01. Code that imported
`Page` for AST work (custom backends implementing
`QueryCompiler.compile_page`, direct AST construction) must switch to
`PageSpec`; everything else is strictly additive. See the updated
[Sorting & Pagination tutorial](tutorial/sorting-pagination.md) and the new
[Pagination reference](reference/pagination.md). 100% test coverage,
`mypy --strict` clean.

## `0.2.0` — Stage 3 complete: `FilterSet` + example app

Stage 3 caps off with **`FilterSet`** (design doc 01 Option B): a
declarative class holding a **strict allow-list** filter surface apart from
the model — `Meta.model` plus a `fields` mapping keyed by public dotted
param names, where **anything not listed is not filterable** (the safe
posture for public APIs). Values are exact operator lists (or `"__all__"` /
`ops.ALL` for everything the type supports, still `allow_regex`-gated),
validated **at class definition** with the usual rich `ConfigurationError`.
The mapping occupies layer 4 of the precedence ladder — the same layer as
`FilterConfig.operators`, which it replaces on FilterSet routes (that knob,
`exclude`, and `sortable` are rejected inside `Meta.config`; `Meta.sortable`
is the sortable allow-list, and the sortable default becomes "listed and
scalar") — and the model-level absolutes (`ops.NONE`, `sortable=False`)
remain final. **Custom declared filters** add parameters no generated name
covers: `active_since = Filter(field="last_login", op="gte")` — attribute
name (or `param=`) as the public name, value typed by the target field +
operator, compiled through the normal AST path, inheritable via abstract
(`Meta`-less) base classes. `FilterDepends(UserFilter)` returns the same
uniform `FilterQuery` as the zero-config and `Filterable` paths — call
sites never change — and multiple FilterSets per model (public vs. admin)
coexist trivially. A runnable example app (users + addresses + tags +
orders + metadata map, all three declaration styles, no MongoDB needed)
ships under `examples/mongo_app/` and is exercised in CI.

That completes the whole `0.1.x` → `0.2.0` arc — Stage 3 grew the type
system from flat scalars to the full compound surface: arrays of scalars
(`0.1.1`), nested models via dotted paths (`0.1.2`), arrays of nested
models (`elem` → single `$elemMatch`) and `dict[str, T]` maps (`0.1.3`),
and now allow-list filter surfaces (`0.2.0`). Strictly additive: no
behavior changes to anything shipped in `0.1.3`. See the new
[FilterSet tutorial](tutorial/filtersets.md). 100% test coverage,
`mypy --strict` clean.

## `0.1.3` — Stage 3 checkpoint: arrays of nested models + maps

The compound-type tables of design doc 02 are now complete. **Arrays of
nested models** (`list[NestedModel]`) get element matching via the `elem`
path segment: every condition sharing an `orders__elem__` prefix in one
request compiles into a **single `$elemMatch`** — same-element semantics
(`?orders__elem__amount__gte=100&orders__elem__status=refunded` finds one
order that is both), the loudly-documented opposite of Mongo's independent
dotted-path default. `elem` parameters are **full-tier** given that
subtlety (generated under `default_profile="full"` or an explicit
per-field/per-route operator opt-in); the array field itself keeps the
safe-tier shape operators (`len__*`/`empty`, reusing the 3a compilation)
plus `isnull`/`exists` when `Optional`; the `elem` hop counts as one
`max_depth` boundary exactly like an embedding; `elem` paths are never
sortable; `text_search` never applies inside elements. **Maps**
(`dict[str, T]`) stay unfilterable by default; a `Filterable` annotation
enables `has_key` (key presence, with the key — request input inserted
into a field path — rejected with a 422 when it contains `.`, `$`, or null
bytes), and `Filterable(keys=["region"])` additionally generates typed,
`eq`-only value-at-key parameters (`?metadata__region=emea` →
`{"metadata.region": "emea"}`). Unsupported map shapes (non-`str` keys,
unsupported value types) with a `Filterable` raise `ConfigurationError` at
registration, as does `keys=` on a non-map field. Strictly additive: no
behavior changes to anything shipped in `0.1.2`. See the new
[arrays-of-nested-models](reference/operators.md#arrays-of-nested-models-listnestedmodel)
and [maps](reference/operators.md#maps-dictstr-t) sections of the Operator
Reference and the matching
[tutorial sections](tutorial/filtering.md#arrays-of-nested-models).
100% test coverage, `mypy --strict` clean.

## `0.1.2` — Stage 3 checkpoint: nested models

Nested Pydantic models (embedded documents) are now filterable by **dotted
path**: introspection recurses into a field whose type is another model, so
`?address__city__contains=ams` compiles to `{"address.city": {"$regex":
"ams"}}` — and everything shipped for flat fields carries over to nested
leaves unchanged (scalar operators, the array family for a nested
`list[T]`, `Filterable(...)`, `type_profiles`, bare-`eq` sugar, sorting by
public name, `FilterConfig` keys in the dotted-param spelling
`"address__city"`). Recursion is **depth-bounded** by the new
`FilterConfig(max_depth=...)` knob (default 2 embedded-model levels below
the root; deeper fields are silently skipped), which keeps the parameter
surface finite and makes self-referential models safe by construction. The
embedding field itself exposes no operators — except `isnull`/`exists` on
an `Optional` embedding — and a whole subtree opts out with
`Filterable(ops=ops.NONE)` on the embedding field or
`FilterConfig(exclude=["address"])`. Generated-name collisions (a literal
`address__city` field vs. a nested `address.city` path) raise a
`ConfigurationError` at registration naming both sources. `list[NestedModel]`
and `dict` stay unfilterable until `v0.1.3`. Strictly additive: no behavior
changes to anything shipped in `0.1.1`. See the new
[nested-models section in the Operator Reference](reference/operators.md#nested-pydantic-models-embedded-documents)
and the [nested-models section of the filtering tutorial](tutorial/filtering.md#nested-models).
100% test coverage, `mypy --strict` clean.

## `0.1.1` — Stage 3 checkpoint: arrays of scalars

`list[T]` and `set[T]` fields (any supported scalar element type, including
`Optional[list[T]]`) are now filterable with their own **membership and
shape** operator family: `has`, `has_any`, `has_all`, `len__eq` and the
`empty` operator in the `safe` tier, plus the `len__ne`/`len__gt`/`len__gte`/
`len__lt`/`len__lte` comparisons in the `full` tier (they compile to `$expr`
over `$size`, which can't use an index). The `empty` operator pins down the
classic empty-vs-missing Mongo trap — a missing field matches neither
`empty=true` nor `empty=false` — and array fields deliberately get **no
scalar operators** (`tags__contains` doesn't exist) and are **not sortable
by default** (`Filterable(sortable=True)` opts in). `max_list_length`
guards `has_any`/`has_all` exactly as it does `in`/`nin`. Strictly additive:
no behavior changes to anything shipped in `0.1.0`. See the new
[array tables in the Operator Reference](reference/operators.md#arrays-of-scalars-listt-sett)
and the [arrays section of the filtering tutorial](tutorial/filtering.md#array-fields).
100% test coverage, `mypy --strict` clean.

## `0.1.0` — first minor release: Stages 1–2 finalized

Stages 1–2 are complete and consolidated: the core filter → sort → paginate
pipeline, the MongoDB query compiler, zero-config `FilterQuery[Model]` /
`FilterDepends(Model)` for scalar fields, per-field `Filterable(...)`
control, per-type profiles, strict mode, and this documentation site. No
functional changes from `0.0.3` — this release is the polish milestone:
every page on this site now describes exactly the shipped, installable API,
with the "not released yet" caveats retired.

## `0.0.3` — Stage 2 checkpoint: per-field control

The filter surface is now curatable. `Annotated[T, Filterable(...)]`
metadata on the model controls each field: `ops=[...]` for an exact operator
allow-list (with `ops.ALL` / `ops.NONE` markers, the latter making a field
explicitly unfilterable), `source=` to point the compiled Mongo query at a
different document key, `param=` to rename the public URL parameter, and
`sortable=` to override sortability per field (including sort-only fields).
`FilterConfig` grows `type_profiles={...}` for per-type operator overrides
and `unknown_params="strict"` — a standard 422 on unrecognized `field__op`
parameters instead of silently ignoring them. Precedence is a four-layer
ladder (route per-field > field `Filterable` > per-type profile > global
profile), every misconfiguration still fails at route registration, and the
error messages now name the field, the operator, and the valid alternatives.
See the new [Controlling the filter surface](tutorial/controlling-fields.md)
tutorial. 100% test coverage, `mypy --strict` clean.

## `0.0.2` — Stage 1 checkpoint: the core works

The README example is real: zero-config `FilterQuery[Model]` /
`FilterDepends(Model)` for **scalar fields** (`str`, numerics, `bool`,
datetimes, `UUID`, `Enum`/`Literal`, `Optional`), typed filter/sort/pagination
query parameters in OpenAPI, standard 422s on bad input, and a MongoDB
compiler producing plain query dicts (no driver dependency). Safety defaults
on: `safe` operator profile, regex gated off, list/filter/limit caps,
sortable allow-list. 100% test coverage, `mypy --strict` clean.

Still to come for `v0.1.0`: per-field `Filterable(...)` control and final
polish — see the roadmap below.

## `0.0.1` — placeholder release

The current state of the project on PyPI: the package name is reserved and
the repository has CI + fully automated release tooling
(`scripts/release.sh`, tag-triggered PyPI publish via Trusted Publishing),
but **no library functionality yet**. Nothing in this documentation site is
installable against `0.0.1` — see [Getting Started](getting-started.md).

## Coming next

Stage 5 ships the second backend in **`v0.4.0`**: a **conformance test
suite** first (a fixed battery of `FilterAST → expected shape` cases every
adapter must pass, run against `MongoCompiler` to lock in behavior), then a
**SQLAlchemy** adapter (`Condition` → `ColumnElement`, capability
declaration, `[sqlalchemy]` extra; flat tables + JSON/JSONB first) — the
same example endpoints running on Mongo and SQLAlchemy with only a backend
swap. Tracked in
[design doc 05 — Roadmap & Release Plan](design/05-roadmap-and-release.md).
