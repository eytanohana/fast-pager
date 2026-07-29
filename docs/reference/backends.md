---
icon: lucide/database
---

# Backends

The endpoint surface `fast-pager` generates is **backend-neutral**: a request
parses into a [`FilterAST`](../design/03-architecture.md), and a small
per-database *compiler* turns that AST into a query. Your endpoint signature
never changes when the database does — the handler body picks the compile
call:

=== "MongoDB"

    ```python
    @app.get("/users")
    async def list_users(q: FilterQuery[User] = FilterDepends(User)):
        return await q.paginate(db.users)          # or q.to_mongo(), q.sort_mongo()
    ```

=== "SQLAlchemy"

    ```python
    @app.get("/users")
    def list_users(q: FilterQuery[User] = FilterDepends(User, backend=BACKEND)):
        stmt = q.apply_sqlalchemy(select(UserRow))  # WHERE + ORDER BY + LIMIT/OFFSET
        return session.execute(stmt).scalars().all()
    ```

Two first-party backends ship today:

| | MongoDB | SQLAlchemy |
|---|---|---|
| Module | `fast_pager.backends.mongo` | `fast_pager.backends.sqlalchemy` |
| Compiler | `MongoCompiler` (built in, no driver dependency) | `SQLAlchemyCompiler(model)` — `pip install 'fast-pager[sqlalchemy]'` |
| `FilterQuery` methods | `to_mongo()`, `sort_mongo()`, `skip`/`limit`, `paginate()` | `to_sqlalchemy(model)`, `sort_sqlalchemy(model)`, `apply_sqlalchemy(stmt)` |
| Output | plain dicts / `[(field, 1|-1)]` lists | `ColumnElement[bool]` / `asc()`/`desc()` expressions |

## The SQLAlchemy backend

`SQLAlchemyCompiler` is bound to the SQLAlchemy half of your model pair — an
ORM mapped class (SQLModel classes work too) or a Core `Table` — and
resolves each filter's source path against its columns:

```python
from sqlalchemy import JSON, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from fast_pager.backends.sqlalchemy import SQLAlchemyCompiler

class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    age: Mapped[int]
    address: Mapped[dict] = mapped_column(JSON)     # nested model → JSON column

BACKEND = SQLAlchemyCompiler(UserRow)
```

`q.apply_sqlalchemy(select(UserRow))` applies the WHERE clause, ORDER BY
keys, and the LIMIT/OFFSET window in one call (the model is inferred from a
single-entity `select()`; pass `model=` explicitly for joins). The pieces
are also available separately: `q.to_sqlalchemy(UserRow)` returns the
boolean expression (or `None` when no filters apply) and
`q.sort_sqlalchemy(UserRow)` the order-by list. A runnable app executing
against in-memory SQLite lives in
[`examples/sqlalchemy_app/`](https://github.com/eytanohana/fast-pager/tree/main/examples/sqlalchemy_app).

### Substring matching: escaping and case sensitivity

`contains` / `startswith` / `endswith` compile through SQLAlchemy's
`autoescape` LIKE helpers: `%`, `_`, and the escape character in user input
are escaped, so the value matches as a **literal** substring — the same
guarantee the Mongo compiler provides via `re.escape()`.

Case sensitivity is where SQL dialects genuinely differ, so `fast-pager`
does not paper over it:

- the **`i*` variants** (`icontains`, `istartswith`, `iendswith`) are
  reliably case-insensitive: `ILIKE` on PostgreSQL,
  `lower(col) LIKE lower(pattern)` elsewhere;
- the **plain variants** compile to `LIKE`, whose sensitivity follows the
  database: case-**sensitive** on PostgreSQL, but **SQLite's `LIKE` is
  case-insensitive for ASCII by default** (`PRAGMA case_sensitive_like`
  flips it), and MySQL/MariaDB follow the column collation (usually
  insensitive).

If your API contract needs guaranteed-insensitive matching, expose the `i*`
operators (`full` tier); treat the plain operators as "database-native
LIKE".

### Nested models: JSON columns

A dotted filter path (`?address__city=Amsterdam` → source `address.city`)
resolves its root segment to a column, which must be **JSON-typed**
(`sa.JSON`, or a subclass like PostgreSQL `JSONB`); the rest of the path
becomes JSON access — `json_extract(users.address, '$."city"')` on SQLite,
`->`/`->>` on PostgreSQL. Comparisons CAST the element based on the value's
type; datetimes compare as ISO-8601 strings (which order correctly); enums
compare by value. Dotted paths whose root column is *not* JSON raise
`CompilationError` — relationship JOINs are a planned follow-up
([design doc 04](../design/04-backend-roadmap.md)).

Dialect notes: the JSON path is exercised on SQLite in CI and uses only
SQLAlchemy's generic JSON operators (PostgreSQL-compatible). Sorting by a
JSON path sorts the extracted element; on PostgreSQL `->>` yields text, so
numeric JSON values sort lexicographically — promote hot numeric sort keys
to real columns.

## Capabilities: what each backend declares

A compiler declares its `name`, `supported_ops`, and `capabilities`
(structural features that aren't a single operator: `nested_paths`,
`elem_match`). Anything not declared is **rejected loudly** — a
`CompilationError` naming the operator and backend — never silently dropped
([design doc 04](../design/04-backend-roadmap.md)).

| Operator / feature | MongoDB | SQLAlchemy |
|---|:---:|:---:|
| `eq`, `ne`, `gt`, `gte`, `lt`, `lte` | ✅ | ✅ |
| `in`, `nin`, `between` | ✅ | ✅ |
| `contains`, `startswith`, `endswith` | ✅ (escaped `$regex`) | ✅ (escaped `LIKE`; [case caveats](#substring-matching-escaping-and-case-sensitivity)) |
| `icontains`, `istartswith`, `iendswith` | ✅ (`$options: "i"`) | ✅ (`ILIKE` / `lower()`) |
| `isnull` | ✅ | ✅ (`IS [NOT] NULL`) |
| `exists` | ✅ (`$exists`) | ❌ — SQL columns always exist; use `isnull` |
| `regex` | ✅ (gated) | ❌ — regex SQL is dialect-specific (`~` vs `REGEXP`; SQLite has none) |
| `text_search` | ✅ (`$text` index) | ❌ — no generic SQL full-text query |
| `has`, `has_any`, `has_all` | ✅ | ❌ — no portable SQL array predicates |
| `len__eq`, `len__*`, `empty` | ✅ | ❌ |
| `has_key` (maps) | ✅ | ❌ — JSON key existence is dialect-specific |
| Nested dotted paths (`nested_paths`) | ✅ dot notation | ✅ via JSON columns |
| `list[NestedModel]` element matching (`elem_match`) | ✅ single `$elemMatch` | ❌ — no `$elemMatch` equivalent; same-element semantics are not faked |

!!! tip "Catch mismatches at startup, not per request"
    Pass the backend to `FilterDepends` and the generated parameter surface
    is intersected with the backend's declaration **at registration time**:

    ```python
    q: FilterQuery[User] = FilterDepends(User, backend=SQLAlchemyCompiler(UserRow))
    ```

    A parameter the backend can't compile (say `tags__has` on SQLAlchemy)
    raises `ConfigurationError` before the app serves traffic, naming every
    offending parameter — trim the surface (`Filterable(ops=...)`, a
    `FilterSet` allow-list, `FilterConfig.exclude`) or pick a backend that
    supports it. The hook is optional and purely a validation: `q` stays
    backend-agnostic, and without it the same mismatch still fails loudly at
    compile time.

## The conformance suite

Backend quality is enforced by a **shippable conformance suite**
(`fast_pager.conformance`): a fixed battery of `FilterAST` inputs covering
every operator and container the core can produce — scalar operators,
escaping/anchoring, same-field merging, the array family, dotted paths,
`$elem` grouping, maps, unsafe-input rejection, sort, and paging. Both
first-party backends run the full battery in CI, and any third-party adapter
can run it to claim compatibility ([design doc
04](../design/04-backend-roadmap.md)):

```python
import pytest
from fast_pager.conformance import CASES, UNCHECKED, run_case

EXPECTED = {"scalar-eq": ..., "merge-same-field-range": ...}  # your shapes

@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_conformance(case):
    run_case(MyCompiler(), case, EXPECTED.get(case.id, UNCHECKED))
```

The battery fixes the **inputs and the backend-neutral semantics** — every
declared case must compile, every undeclared case must raise
`CompilationError` naming the operator, invalid inputs (e.g. unsafe map
keys) must be rejected everywhere — while each adapter supplies its own
**expected output table** (pass `compare=` when outputs lack value equality,
e.g. comparing rendered SQL). A full adapter-authoring guide ships with the
cursor-pagination release ([roadmap](../design/05-roadmap-and-release.md)).
