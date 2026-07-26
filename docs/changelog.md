---
icon: lucide/history
---

# Changelog

`fast-pager` follows [SemVer](https://semver.org/); `0.x` versions signal an
unstable API, and breaking changes bump the minor version and are called out
below. See the [Roadmap](design/05-roadmap-and-release.md) for what's coming
next, and the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)
for the execution detail behind each stage.

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

The rest of Stage 3, in strictly additive checkpoint releases:
`list[NestedModel]` element matching via `elem` → `$elemMatch` and `dict`
fields with enumerated keys in **`v0.1.3`**; and the `FilterSet` class
(allow-list `fields` mapping, multiple filter surfaces per model, custom
declared filters) capping the stage in **`v0.2.0`**. Tracked in
[design doc 05 — Roadmap & Release Plan](design/05-roadmap-and-release.md).
