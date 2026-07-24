---
title: fast-pager
description: Turn your Pydantic models into filterable, sortable, paginated FastAPI query parameters — automatically.
icon: lucide/list-filter-plus
---

# fast-pager

**Turn your Pydantic models into filterable, sortable, paginated FastAPI query
parameters — automatically.**

`fast-pager` reads the Pydantic models you already use in your FastAPI routes
and generates **type-safe query parameters** for filtering, sorting and
pagination. Those parameters show up in your OpenAPI docs for free, and
compile down to a real database query (MongoDB first, more backends later).

!!! warning "Targeting the v0.1 release"
    `fast-pager` is under active development — the current PyPI release is a
    `0.0.x` placeholder. Everything on this site describes the **upcoming
    `v0.1.0`** API as specified in the [design documents](design/index.md) and
    the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md).
    See the [Changelog](changelog.md) for the current, real status.

```python
class User(BaseModel):
    name: str
    age: int

@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

A request to:

```text
GET /users?name__contains=ana&age__gte=21&age__lt=65&sort=-age&limit=20
```

…compiles to:

```python
{"name": {"$regex": "ana"}, "age": {"$gte": 21, "$lt": 65}}
# sort=[("age", -1)], skip=0, limit=20
# (values in `contains` filters are regex-escaped before compilation)
```

…and every one of those parameters is documented, validated and typed in
`/docs`.

## Why fast-pager

<div class="grid cards" markdown>

-   :lucide-shield-check:{ .lg .middle } **Zero-config, safe by default**

    ---

    Point `FilterDepends` at a model and get a sensible, safe set of query
    parameters immediately — no schema duplication, no hand-written filter
    class. Dangerous operators like `regex` are opt-in, never free.

-   :lucide-file-check-2:{ .lg .middle } **Honest OpenAPI docs**

    ---

    Every generated parameter is typed, validated and documented in
    `/docs` for free. The docs cannot drift from behavior because both are
    generated from the same Pydantic model.

-   :lucide-layers:{ .lg .middle } **Progressive disclosure**

    ---

    Start with zero-config `FilterQuery[Model]`. Add
    `Annotated[T, Filterable(...)]` when you want to curate operators.
    Graduate to a `FilterSet` for decoupling and multiple views per model —
    every path yields the same uniform query object, so call sites never
    need to change.

-   :lucide-database:{ .lg .middle } **Backend-agnostic core**

    ---

    Filtering, sorting and pagination compile to a plain, inspectable AST.
    Mongo is the first backend adapter; the model→params→AST pipeline
    itself imports no database code.

-   :lucide-list-filter:{ .lg .middle } **One convention: `field__op`**

    ---

    `name__contains=ana`, `age__gte=21`, `sort=-age`. The Django-style
    double-underscore convention is familiar, and because the whole
    parameter surface is pre-generated from the model, there is no
    request-time string parsing to get wrong.

-   :lucide-package:{ .lg .middle } **One dependency, one call**

    ---

    The 80% case is `FilterDepends(User)` and `q.to_mongo()`. No ORM, no
    query DSL to learn — you get back a plain query dict (or an AST, if you
    want full control).

</div>

## See it in action

<div class="grid cards" markdown>

-   :lucide-rocket:{ .lg .middle } **[Getting Started](getting-started.md)**

    ---

    Install `fast-pager` and wire up your first filterable endpoint.

-   :lucide-book-open:{ .lg .middle } **[Tutorial](tutorial/filtering.md)**

    ---

    Learn the `field__op` convention, then sorting, `limit` and `offset`.

-   :lucide-table:{ .lg .middle } **[Operator Reference](reference/operators.md)**

    ---

    Every scalar type and the operators it exposes, safe vs. full tier.

-   :lucide-compass:{ .lg .middle } **[Design Documents](design/index.md)**

    ---

    The full product design this implementation is built from.

</div>

## Project status

`fast-pager` is in the **design phase** — see the
[README](https://github.com/eytanohana/fast-pager#status) for the current
state and the [Changelog](changelog.md) for what has actually shipped.
