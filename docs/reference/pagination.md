---
icon: lucide/book-open
---

# Pagination Reference

The API surface behind the [Sorting & Pagination tutorial](../tutorial/sorting-pagination.md):
the `Page[T]` response envelope, the `q.paginate(...)` runner, and the
pagination parameter strategies.

## `Page[T]`

```python
from fast_pager import Page
```

A **generic Pydantic model** — the opt-in response envelope. Because it is
a real Pydantic model, `response_model=Page[User]` gives you response
validation and a correct OpenAPI schema (`Page_User_` in
`components.schemas`, with `items` typed as `array of User`).

| Field | Type | Meaning |
|---|---|---|
| `items` | `list[T]` | The page of results |
| `total` | `int \| None` | The collection-wide count matching the filter — `None` when counting was skipped (`total="none"`) |
| `limit` | `int` | The window size that produced `items` |
| `offset` | `int` | Items skipped before the window |

`limit`/`offset` always describe the returned window, whichever parameter
strategy (`limit`/`offset` or `page`/`page_size`) the request used.

!!! note "`Page` vs. `PageSpec`"
    Before `v0.3.0`, `fast_pager.Page` was the internal AST dataclass for
    the pagination window. That node is now
    [`PageSpec`](../design/03-architecture.md) (`fast_pager.ast.PageSpec`,
    re-exported as `fast_pager.PageSpec`), and the public `Page` name is
    this envelope, matching
    [design doc 01](../design/01-developer-experience.md#response-envelope-optional-opt-in).

## `FilterQuery.paginate()`

```python
page: Page = await q.paginate(collection, total="exact")
```

Runs the parsed query — filter (`q.to_mongo()`), sort (`q.sort_mongo()`),
and window (`q.skip` / `q.limit`) — against a Mongo-like collection and
returns a `Page` of whatever documents the driver yields. Always a
coroutine: `await` it for sync *and* async collections alike.

### The duck-typed collection contract

`paginate()` imports **no database driver** (the optional `[mongo]` extra
is purely a convenience for installing pymongo alongside). Any object with
the standard collection surface works — motor, pymongo sync, pymongo
async, or an in-memory fake in your tests:

- `find(filter)` → a cursor supporting `.sort(keys)`, `.skip(n)`,
  `.limit(n)` (chained), iterated via `to_list(...)`, async iteration, or
  plain iteration — whichever the cursor offers, in that order of
  preference;
- `count_documents(filter)` — required for `total="exact"` (and the
  `"estimated"` fallback);
- `estimated_document_count()` — used by `total="estimated"` on
  unfiltered queries.

Methods may return plain values or awaitables; `paginate()` detects
awaitables at runtime. `.sort()` is only invoked when the request actually
sorted. An object missing the needed surface raises a `TypeError` naming
exactly what is missing.

### `total=` modes

| Mode | Behavior |
|---|---|
| `"exact"` (default) | `count_documents` with the same compiled filter |
| `"estimated"` | `estimated_document_count()` **only when the compiled filter is empty**; a filtered query — or a collection without the method — falls back to an exact count, so `total` is never a number unrelated to the results |
| `"none"` | Skips counting entirely; `Page.total` is `None` |

See [the count-cost table](../tutorial/sorting-pagination.md#the-count-is-not-free)
for when to pick which.

## Pagination strategies

`FilterConfig(pagination=...)` selects which parameters a route generates:

| Strategy | Parameters | Defaults & guardrails |
|---|---|---|
| `"offset"` (default) | `limit`, `offset` | `limit` defaults to `default_limit`, capped at `max_limit` (422 above); `offset >= 0` |
| `"page"` | `page`, `page_size` | `page` is 1-based (`page=0` is a 422); `page_size` defaults to `default_limit`, capped at `max_limit` |

Both resolve to the same internals — `q.limit`, `q.offset`, and `q.skip`
report the effective window (`offset = (page - 1) * page_size`), so
compiled queries, backends, and `paginate()` are strategy-agnostic. The
active strategy's parameter names are reserved (a model field named `page`
collides only under `pagination="page"`) and appear in OpenAPI like any
other generated parameter. The parameter names are fixed; making them
configurable is possible future work.
