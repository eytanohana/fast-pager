---
icon: lucide/arrow-up-down
---

# Sorting & Pagination

Sorting and pagination ride on the same `FilterDepends(...)` dependency as
filtering — they're the other two hats a list endpoint wears (see
[the overview](../design/00-overview.md#why-the-name-is-fast-pager)), so they
share the same `q` object instead of being separate dependencies to wire up.

```text
GET /users?sort=-age,name&limit=20&offset=40
```

## Sorting

`sort` is a comma-separated list of field names; a leading `-` means
descending order.

```text
?sort=-age           # age descending
?sort=age             # age ascending
?sort=-age,name       # age descending, then name ascending as a tiebreaker
```

This compiles to:

```python
q.sort_mongo()
# -> [("age", -1), ("name", 1)]
```

Only fields marked **sortable** are accepted — by default, the same set of
fields that are filterable, except array (`list[T]` / `set[T]`) fields,
which are never sortable unless opted in (sorting on a Mongo array uses
min/max element semantics, which surprises people). Sorting on a field
outside that allow-list is rejected, which prevents an endpoint from
accidentally allowing sorts on unindexed fields.

## Pagination

The default pagination strategy is `offset` / `limit`, which maps directly
to Mongo's `skip`/`limit`:

```text
?limit=20&offset=40
```

```python
q.skip   # -> 40
q.limit  # -> 20

await db.users.find(q.to_mongo()).sort(q.sort_mongo()).skip(q.skip).limit(q.limit).to_list(None)
```

Two guardrails apply out of the box:

- **`default_limit`** — used when the client omits `limit`.
- **`max_limit`** — the hard ceiling; an unbounded `limit` is never allowed,
  even if a client asks for one.

### The `page` / `page_size` strategy

If your clients think in page numbers rather than offsets, switch the
generated parameters with one config knob:

```python
@app.get("/users")
async def list_users(
    q: FilterQuery[User] = FilterDepends(User, config=FilterConfig(pagination="page")),
): ...
```

```text
?page=3&page_size=20      # items 40–59
```

`page` is 1-based (`page=0` is a 422) and `page_size` obeys the same
guardrails as `limit` (`default_limit` when omitted, `max_limit` as the
ceiling). It is pure sugar over offset: `q.limit`, `q.offset`, and `q.skip`
resolve to the same window (`offset = (page - 1) * page_size`), so backends
and `paginate()` below don't care which strategy a route uses. Keyset/cursor
pagination is on the roadmap — see
[design doc 01](../design/01-developer-experience.md#pagination-sorting-first-class-per-the-name)
for the full comparison.

## Optional: a paginated response envelope

By default `fast-pager` returns just the query — you decide how to shape the
response. For the common "list + total count" shape, `Page[T]` and
`q.paginate(collection)` are the opt-in sugar:

```python
from fast_pager import FilterDepends, FilterQuery, Page

@app.get("/users", response_model=Page[User])
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await q.paginate(db.users)   # find + count, one line
```

```json
{
  "items": [ "..." ],
  "total": 137,
  "limit": 20,
  "offset": 40
}
```

`Page[T]` is a **generic Pydantic model**, so `response_model=Page[User]`
keeps response validation and the OpenAPI schema exactly right.
`paginate()` runs the compiled filter, sort, and window (`to_mongo()` /
`sort_mongo()` / `skip` / `limit` — which remain available for full
control) against the collection you hand it.

The collection is **duck-typed** — no driver is imported, awaitables are
detected at runtime — so the same line works across drivers:

=== "motor / pymongo async"

    ```python
    client = AsyncIOMotorClient(...)          # or AsyncMongoClient(...)
    db = client.get_database("app")

    @app.get("/users", response_model=Page[User])
    async def list_users(q: FilterQuery[User] = FilterDepends(User)):
        return await q.paginate(db.users)
    ```

=== "pymongo (sync)"

    ```python
    client = MongoClient(...)
    db = client.get_database("app")

    @app.get("/users", response_model=Page[User])
    async def list_users(q: FilterQuery[User] = FilterDepends(User)):
        # Still awaited — paginate() drives sync collections too.
        return await q.paginate(db.users)
    ```

### The count is not free

An exact `total` runs `count_documents` with the same filter on **every
page request** — expensive on large collections. So `Page.total` is
optional and the cost is a knob:

```python
return await q.paginate(db.users, total="none")
```

| `total=` | What runs | Cost | `Page.total` |
|---|---|---|---|
| `"exact"` (default) | `count_documents(filter)` | A full count matching the filter, every request | The exact count |
| `"estimated"` | `estimated_document_count()` — **only when no filters are applied**; otherwise falls back to `"exact"` | Metadata read: effectively free (but never filter-aware) | An estimate (or the exact fallback) |
| `"none"` | No count at all | Free | `None` |

The `"estimated"` fallback rule is deliberate: Mongo's
`estimatedDocumentCount` cannot take a filter, so estimating a *filtered*
query would return a number unrelated to the results. When the compiled
filter is non-empty (or the collection offers no estimate method),
`paginate()` quietly runs the exact count instead — you always get a
truthful `total`. `"none"` is the right default for infinite-scroll UIs,
and a natural fit for the future cursor strategy.

## Next

The [Pagination reference](../reference/pagination.md) documents the
`Page[T]` fields, the full `paginate()` contract, and the duck-typed
collection surface. See the [Operator Reference](../reference/operators.md)
for every filter operator by type, or revisit [Filtering](filtering.md) for
the `field__op` convention.
