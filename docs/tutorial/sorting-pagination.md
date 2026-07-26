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

A `page` / `page_size` strategy (sugar over `offset`) and keyset/cursor
pagination are on the roadmap — see
[design doc 01](../design/01-developer-experience.md#pagination-sorting-first-class-per-the-name)
for the full comparison.

## Optional: a paginated response envelope

By default `fast-pager` returns just the query — you decide how to shape the
response. For the common "list + total count" shape, an opt-in helper is
planned for a later release:

```python
@app.get("/users", response_model=Page[User])
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await q.paginate(db.users)
```

```json
{
  "items": [ "..." ],
  "total": 137,
  "limit": 20,
  "offset": 40
}
```

The exact count is not free on a large collection, so `paginate(...)` will
take a `total="exact" | "estimated" | "none"` knob rather than always
running a `count_documents`. This ships in a later stage (see the
[roadmap](../design/05-roadmap-and-release.md)) — `to_mongo()` /
`sort_mongo()` / `skip` / `limit` are the shipped building blocks and remain
available regardless.

## Next

See the [Operator Reference](../reference/operators.md) for every filter
operator by type, or revisit [Filtering](filtering.md) for the `field__op`
convention.
