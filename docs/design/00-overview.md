# 00 — Overview & Vision

## The problem

Teams that build FastAPI services on top of MongoDB (or any document store) repeat
the same boilerplate on every list endpoint:

1. They define a Pydantic model for the resource.
2. They hand-write query parameters for filtering (`?min_age=`, `?name_like=`, …).
3. They hand-write the translation from those params into a Mongo query dict.
4. They hand-write pagination (`skip`/`limit`) and sorting.
5. They keep all of the above in sync with the model as it changes — and usually
   don't, so the filtering surface drifts and the docs lie.

This is mechanical, error-prone work. The model already declares the field names
and types. **The filtering surface is derivable from the model.** That derivation
is what `fast-pager` automates.

## The idea in one sentence

> Given a Pydantic model, generate FastAPI query parameters for filtering, sorting
> and pagination, surface them in OpenAPI, validate/coerce them, and compile them
> into a backend query — with sensible defaults and precise per-field control.

## Why the name is "fast-pager"

The name is a deliberate scope signal: this is not *only* a filtering library.
A list endpoint is a **page over a filtered, sorted result set**. Filtering,
sorting, and pagination are the same feature wearing three hats, and they share
one derivation (the model) and one output (a backend query). Treating them as a
single coherent surface — rather than three bolt-ons — is the product.

```
            ┌────────────────────────────────────────────┐
  Pydantic  │  filter   →   sort   →   paginate          │    Backend
  model  ───┤  (where)      (order)    (skip/limit)      ├──► query
            └────────────────────────────────────────────┘
                         fast-pager
```

## Goals

- **Zero-config first.** Point the library at a model and get a reasonable,
  safe set of query params immediately. No schema duplication required.
- **Precise when you need it.** Override which operators are exposed per field,
  rename params, map to different DB field names, cap expensive operators.
- **Honest docs.** Every generated parameter appears in OpenAPI with correct
  type, description, and constraints. The docs cannot drift from behavior because
  both are generated from the same source.
- **Backend-agnostic core.** The model→params→AST pipeline knows nothing about
  Mongo. A thin adapter compiles the AST. New databases = new adapter, not a fork.
- **A pleasure to read.** Small, typed, well-documented surface. The 80% case is
  one import and one dependency.

## Non-goals (at least for 1.0)

- **Not an ORM/ODM.** We do not manage connections, schemas, migrations, or
  persistence. We produce a query; you run it with your driver of choice.
- **Not a full query language.** Arbitrary boolean nesting (`(a OR b) AND c`) over
  HTTP query strings is a rabbit hole. v1 supports AND-combined filters with a
  clear, documented extension path to OR groups (see doc 03). We resist building
  GraphQL-in-a-querystring.
- **Not aggregation/joins.** Grouping, `$lookup`, computed fields are out of scope.
- **No write operations.** Read-side only.

## Guiding principles

1. **The model is the single source of truth.** Everything is derived from it.
2. **Make the safe thing the default thing.** Expensive or dangerous operators
   (regex, unbounded `$in`, full collection scans on unindexed fields) are
   opt-in or guarded, not free.
3. **Progressive disclosure.** Beginners write one line. Experts reach for a
   `FilterSet` and tune every field. Nothing in between forces a rewrite.
4. **Fail loud at startup, not at runtime.** Misconfiguration (operator not valid
   for a type, unknown field) is caught when the route is registered, surfaced as
   a clear exception — not as a 500 on the first weird request.
5. **Composable, not magic.** The output is a plain query object you can inspect,
   log, modify, and test. No hidden global state.

## Who is this for

- Teams with many CRUD-ish FastAPI + Mongo endpoints who are tired of the
  boilerplate and want consistent, documented filtering across services.
- Library/platform authors who want to offer filtering as a feature without
  inventing their own DSL.

## What success looks like

- A new list endpoint with rich filtering is **one dependency and one `.to_mongo()`
  call**.
- The maintainer changes a field type on the model; the filtering surface and docs
  update automatically and correctly.
- Adopting a second backend (e.g. SQLAlchemy) is a one-line swap at the call site,
  not a rewrite of the endpoints.

Continue to **[01 — Developer Experience](01-developer-experience.md)**.
