---
icon: lucide/history
---

# Changelog

`fast-pager` follows [SemVer](https://semver.org/); `0.x` versions signal an
unstable API, and breaking changes bump the minor version and are called out
below. See the [Roadmap](design/05-roadmap-and-release.md) for what's coming
next, and the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)
for the execution detail behind each stage.

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

## Coming in `v0.1.0`

Stages 1–2 finalized: the core filter → sort → paginate pipeline, the
MongoDB query compiler, zero-config `FilterQuery[Model]` /
`FilterDepends(Model)` for scalar fields, per-field `Filterable(...)`
control, per-type profiles, strict mode, and this documentation site —
polished, with the docs describing exactly the shipped API. Most of the
content on this site describes this upcoming release. Tracked in
[design doc 05 — Roadmap & Release Plan](design/05-roadmap-and-release.md).
