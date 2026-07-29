# fast-pager example app — SQLAlchemy backend

The [`mongo_app`](../mongo_app/) endpoints on **SQLAlchemy**: the same
`User` Pydantic model (nested `Address` included), the same declaration
styles, the same `q` object in every handler — only the backend swaps.

| Endpoint | Style |
|---|---|
| `GET /users` | Zero-config `FilterDepends(User, backend=...)` + `Filterable` metadata |
| `GET /public/users` | `PublicUserFilter` — a small, strict allow-list `FilterSet` |

## A real database this time

Unlike the Mongo demo (which returns the query it *would* run), this app
**executes** the compiled query against a seeded in-memory SQLite database
and returns real rows:

```python
stmt = q.apply_sqlalchemy(select(UserRow))       # WHERE + ORDER BY + LIMIT/OFFSET
rows = session.execute(stmt).scalars().all()
```

The nested `address` model is stored in a JSON column, so dotted filters
like `?address__city=Amsterdam` compile to JSON path access
(`json_extract(users.address, '$."city"')`).

## The capability model in action

SQL cannot express everything Mongo can — array membership operators,
`list[Order]` same-element matching, map key existence. Those fields simply
aren't part of this app's model, and `FilterDepends(..., backend=BACKEND)`
validates **at startup** that every generated parameter is compilable on
this backend (try adding `tags: list[str]` to `User` — the app fails to
import with a `ConfigurationError` naming the offending parameters). See the
[backends reference](https://fast-pager.eytanohana.com/reference/backends/)
for the full capability matrix.

## Running it

From this directory (with `fast-pager[sqlalchemy]`, `fastapi`, and `uvicorn`
installed — the repo's dev environment has all three):

```bash
uvicorn main:app --reload
```

Then explore:

- <http://127.0.0.1:8000/docs> — every generated filter/sort/pagination
  parameter, typed and documented;
- `GET /users?name__startswith=Ana&age__gte=30&sort=-age` — real filtered,
  sorted rows;
- `GET /public/users?address__city=Amsterdam` — the strict public surface.

This app is exercised by `tests/test_example_app.py` in CI.
