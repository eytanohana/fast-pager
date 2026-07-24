---
icon: lucide/rocket
---

# Getting Started

!!! warning "Targeting v0.1.0 — not on PyPI yet"
    This page describes the API `fast-pager` will ship in `v0.1.0`
    (see the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)).
    The package name is reserved on PyPI as a `0.0.x` placeholder with no
    functionality yet — don't `pip install` expecting this to work today.
    Check the [Changelog](changelog.md) for what's actually released.

## Install

Once `v0.1.0` is published, install `fast-pager` alongside FastAPI with your
package manager of choice.

=== "uv"

    ```bash
    uv add fast-pager
    ```

=== "pip"

    ```bash
    pip install fast-pager
    ```

`fast-pager` depends on `fastapi>=0.115` and `pydantic>=2.7`. The first
release ships a MongoDB query compiler; no database driver (`pymongo`,
`motor`) is required by the library itself — you bring your own client.

## Your first endpoint

Start from a Pydantic model you likely already have:

```python
from fastapi import FastAPI
from pydantic import BaseModel
from fast_pager import FilterDepends, FilterQuery

app = FastAPI()

class User(BaseModel):
    name: str
    age: int

@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

That's it — no query-parameter boilerplate, no hand-written Mongo dict. The
`FilterDepends(User)` dependency introspects `User`'s fields, generates a
safe default set of `field__op` query parameters for each one, and returns a
`FilterQuery[User]` object (`q`) once FastAPI parses the request.

## What shows up in `/docs`

Because every parameter is generated from `User`'s fields at route
registration time, FastAPI's automatic OpenAPI docs (`/docs`, `/redoc`) show
each one with its real type and description — nothing is hidden behind a
generic `filters: dict` parameter. For the `User` model above you'd see
parameters like:

| Parameter | Type | Meaning |
|---|---|---|
| `name` | `string` | `name == value` (bare equality is sugar for `eq`) |
| `name__contains` | `string` | substring match (value is regex-escaped) |
| `name__startswith` / `name__endswith` | `string` | prefix / suffix match |
| `name__in` / `name__nin` | `array[string]` | membership / exclusion |
| `age` | `integer` | `age == value` |
| `age__gt` / `age__gte` / `age__lt` / `age__lte` | `integer` | comparisons |
| `age__in` / `age__nin` | `array[integer]` | membership / exclusion |
| `sort` | `string` | comma-separated sort keys, `-` prefix for descending |
| `limit` / `offset` | `integer` | pagination, capped by safe defaults |

Every operator available per type is documented in the
[Operator Reference](reference/operators.md). Sending a bad value (e.g.
`age__gte=banana`) returns FastAPI's standard `422` — there's no custom error
format to learn, because each generated parameter is a properly typed
`Query(...)` under the hood.

## Compiling the query

`q` is uniform no matter how `User` was configured:

```python
q.to_mongo()        # -> dict ready for pymongo/motor .find()
q.sort_mongo()       # -> list[tuple[str, int]] for .sort()
q.skip, q.limit      # -> ints for pagination
q.to_ast()           # -> backend-agnostic FilterAST (for custom adapters/testing)
q.applied            # -> the parsed, validated filters (introspectable)
```

## Next steps

- [Tutorial: Filtering](tutorial/filtering.md) — the `field__op` convention
  in depth, with worked examples per type.
- [Tutorial: Sorting & Pagination](tutorial/sorting-pagination.md) — `sort`,
  `limit`, `offset`.
- [Operator Reference](reference/operators.md) — every operator, by type.
