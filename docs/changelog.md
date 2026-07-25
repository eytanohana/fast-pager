---
icon: lucide/history
---

# Changelog

`fast-pager` follows [SemVer](https://semver.org/); `0.x` versions signal an
unstable API, and breaking changes bump the minor version and are called out
below. See the [Roadmap](design/05-roadmap-and-release.md) for what's coming
next, and the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)
for the execution detail behind each stage.

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

The first functional release: the core filter → sort → paginate pipeline,
a MongoDB query compiler, zero-config `FilterQuery[Model]` /
`FilterDepends(Model)` for scalar fields, and this documentation site. Most
of the content on this site describes this upcoming release. Tracked in
[design doc 05 — Roadmap & Release Plan](design/05-roadmap-and-release.md).
