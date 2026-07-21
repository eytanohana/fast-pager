# 04 — Backend Roadmap

Can this generalize past Mongo — to any database, or at least a steadily
growing list? Yes, by design. The `FilterAST` + `QueryCompiler` boundary
(doc 03) exists exactly for this. The front half of the library — model introspection, parameter generation,
OpenAPI, validation — is **100% backend-neutral**. Adding a database is writing one
adapter class, not touching the core.

## The contract every backend implements

```python
class QueryCompiler(Protocol):
    name: str
    supported_ops: frozenset[str]
    capabilities: Capabilities            # nested? arrays? text-search? regex?

    def compile_where(self, group: Group) -> Any: ...
    def compile_order(self, order: list[Sort]) -> Any: ...
    def compile_page(self, page: Page) -> Any: ...
```

Two ideas make this robust across very different databases:

1. **Capability declaration.** Not every store supports every operator (a key-value
   store has no `regex`; a SQL table has no array `$elemMatch` unless it's JSONB).
   The adapter declares `supported_ops` and `capabilities`; the core intersects
   these with the user's config **at registration time** and fails loudly if the
   user asked for something the backend can't do. We never silently drop a filter.

2. **Graceful capability tiers.** A backend can advertise a *subset* and still be
   first-class. `list[str]` array operators light up only where the backend can
   express them; everything else still works.

## Phased backend expansion

### Tier 1 — MongoDB (the launch backend)

- Driver-agnostic output: `compile_where` returns a plain `dict`, usable with
  `pymongo` and `motor` alike (sync and async). We do **not** depend on a driver;
  we emit the query, you run it.
- Optional thin conveniences for `motor`/`pymongo` (`q.paginate(collection)`),
  behind an extra: `pip install fast-pager[mongo]`.
- **Beanie / ODMantic** (Pydantic-native Mongo ODMs) are a natural early add:
  their documents *are* Pydantic models, so introspection works unchanged and we
  can emit native ODM query expressions. Strong fit, likely Tier 1.5.

### Tier 2 — SQL via SQLAlchemy

The highest-value second backend by far (largest FastAPI audience).

- Compiles `Condition` → SQLAlchemy `ColumnElement` boolean expressions
  (`column >= value`, `column.ilike(f"%{v}%")`, `column.in_(...)`).
- Input model can be a Pydantic model *paired with* a SQLAlchemy model, or a
  SQLModel class (which is both). The introspector already understands Pydantic;
  the adapter maps field paths → columns.
- Nested-model filtering maps to JOINs or JSON column access depending on schema;
  initial support targets flat tables + JSON/JSONB columns, with relationship
  JOINs as a follow-up.
- `pip install fast-pager[sqlalchemy]`.

### Tier 3 — Elasticsearch / OpenSearch

- A filtering+search engine is an excellent fit: `contains`/`text_search` map to
  real analyzed queries rather than collection-scanning regex.
- Compiles to the ES query DSL (`bool`/`must`/`filter`/`range`/`terms`).

### Tier 4 — community backends

Once the adapter contract is stable and documented, additional stores
(DynamoDB, Postgres-via-asyncpg-raw-SQL, Redis search, etc.) can be **third-party
packages** (`fast-pager-dynamodb`) that just implement `QueryCompiler`. We
publish the adapter authoring guide and a conformance test suite so external
adapters can prove correctness.

## How the user selects a backend

The backend is chosen at the call site / app setup, not baked into endpoints:

```python
# app setup
configure(backend=MongoCompiler())          # or SQLAlchemyCompiler(...), etc.

# endpoint is backend-agnostic
@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return run(q)        # run() dispatches via the configured backend
```

Switching backends — or running two backends in one app — does not touch the
endpoint signatures. This is the payoff of the AST boundary: **the database is a
deployment detail, not an API-design decision.**

## A conformance suite is the real product moat

To make "any DB" credible, we ship a **backend conformance test suite**: a fixed
battery of `(FilterAST → expected-shape)` assertions plus, where feasible,
integration tests against a containerized instance. Any adapter — first-party or
community — runs the suite to claim compatibility. This keeps quality high as the
backend list grows and turns external contributions into a strength rather than a
support burden.

## Honest scope note

We will **not** try to be a universal query abstraction that papers over every
database difference. Some operators simply don't exist everywhere, and pretending
otherwise produces leaky, surprising behavior. The capability model embraces the
differences: every backend does what it genuinely can, declares the rest, and the
user is told at startup. That honesty is more valuable than a false promise of
total portability.

Continue to **[05 — Roadmap & Release Plan](05-roadmap-and-release.md)**.
