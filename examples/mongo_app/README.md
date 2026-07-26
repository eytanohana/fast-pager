# fast-pager example app

A runnable FastAPI app demonstrating every declaration style over one
non-trivial model set — users with a nested address, a `list[str]` of tags,
a `list[Order]`, and a `dict[str, str]` metadata map with enumerated keys:

| Endpoint | Style |
|---|---|
| `GET /users` | Zero-config `FilterDepends(User)` + `Filterable` metadata on the model |
| `GET /public/users` | `PublicUserFilter` — a small, strict allow-list `FilterSet` |
| `GET /admin/users` | `AdminUserFilter` — a wide `FilterSet` over the *same* model, with a custom `?active_since=` filter and strict unknown-param mode |

## No MongoDB required

This demo is deliberately dependency-free: instead of executing the query,
every endpoint **returns the query it would run** — the compiled Mongo
filter dict, the sort list, and the pagination window. That keeps the
example honest (you see exactly what `fast-pager` produces) and lets CI run
it without a database. With a real database, a handler body would be:

```python
await db.users.find(q.to_mongo()).sort(q.sort_mongo() or None) \
    .skip(q.skip).limit(q.limit).to_list(None)
```

## Running it

From this directory (with `fast-pager`, `fastapi`, and `uvicorn` installed —
the repo's dev environment has all three):

```bash
uvicorn main:app --reload
```

Then open http://127.0.0.1:8000/docs and try, for example:

```
GET /users?name__contains=ana&age__gte=21&sort=-age&limit=20
GET /public/users?tags__has=python&address__city=Amsterdam
GET /admin/users?orders__elem__status=paid&orders__elem__amount__gte=100&active_since=2026-01-01T00:00:00Z
```

The app is exercised by `tests/test_example_app.py`, so CI keeps it working.
