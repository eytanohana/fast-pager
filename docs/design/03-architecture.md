# 03 — Architecture

The architecture has one job: keep the **model→params→AST** pipeline completely
ignorant of the database, so the only backend-specific code is a small,
swappable compiler. This is what makes "extend past Mongo" a roadmap item rather
than a rewrite.

## The pipeline

```
┌──────────────┐   introspect   ┌──────────────┐   generate    ┌──────────────┐
│  Pydantic    │ ─────────────► │  FieldSpec   │ ────────────► │  FastAPI     │
│  model       │                │  tree        │  params       │  signature   │
└──────────────┘                └──────────────┘               └──────────────┘
                                       │                               │
                                       │                         request comes in
                                       ▼                               ▼
                                ┌──────────────┐    parse +    ┌──────────────┐
                                │ OperatorReg  │ ◄──────────── │  raw query   │
                                │ (validate)   │   coerce      │  params      │
                                └──────────────┘               └──────────────┘
                                       │
                                       ▼
                                ┌──────────────┐   compile     ┌──────────────┐
                                │  FilterAST   │ ────────────► │  Backend     │
                                │ (neutral)    │   (adapter)   │  query       │
                                └──────────────┘               └──────────────┘
```

Five components, sharply separated:

### 1. Introspector

Walks a Pydantic model (v2 `model_fields`, `FieldInfo`, `annotation`) and produces
a tree of `FieldSpec`:

```python
@dataclass(frozen=True)
class FieldSpec:
    path: tuple[str, ...]      # ("address", "city") for nested
    source: str                # DB field name ("address.city")
    py_type: type              # resolved, Optional-unwrapped base type
    container: Container        # SCALAR | LIST | NESTED | LIST_OF_NESTED | MAP
    nullable: bool
    annotations: FilterableMeta | None   # from Annotated[...]
```

Resolving `Optional`, `list[...]`, `Annotated[...]`, enums, and nested models
happens *here, once*, at registration time. Recursion is depth-bounded and
cycle-safe (doc 02).

### 2. Operator registry

A table of operators, each declaring arity, value type, and **per-backend
compile functions**. The registry decides which operators a `FieldSpec` is
*allowed* to expose (type profile + overrides), and validates user config against
that at registration.

```python
@dataclass(frozen=True)
class Operator:
    name: str                       # "gte"
    arity: Arity                    # SINGLE | LIST | RANGE | BOOL
    value_type: ValueTypeRule       # SAME_AS_FIELD | BOOL | INT
    applies_to: Container           # which containers it's valid on
    tier: Tier                      # SAFE | FULL
```

Operators are extensible: a user (or a backend) can register a custom operator
without forking the library.

### 3. Parameter generator + the FastAPI signature trick

This is the one genuinely clever piece, and it's worth getting right because it's
what makes parameters show up in `/docs` with correct types.

FastAPI builds OpenAPI by **inspecting the callable's signature**. So we
*synthesize* a function whose parameters are exactly the filter params, then hand
it to FastAPI as a dependency.

```python
import inspect
from typing import Optional
from fastapi import Query

def build_dependency(specs: list[ResolvedParam]):
    params = [
        inspect.Parameter(
            name=p.python_safe_name,                 # "name__contains"-safe ident
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=Query(
                None,
                alias=p.url_name,                    # the real "name__contains"
                description=p.help_text,             # shows in /docs
                # constraints (max_length, ge/le) flow from the field/operator
            ),
            annotation=Optional[p.value_annotation], # drives type + validation
        )
        for p in specs
    ]
    # pagination + sort params appended here

    def dependency(**kwargs):
        return FilterQuery.from_raw(kwargs, specs)   # parse → validate → AST

    dependency.__signature__ = inspect.Signature(params)
    return dependency
```

Key points:

- Each param is `Optional[T]` with default `Query(None, alias=..., description=...)`.
  Because the annotation is the real type, **FastAPI/Pydantic do the coercion and
  emit a clean 422** on bad input — we get validation for free.
- `alias` carries the `__`-containing public name (which isn't a valid Python
  identifier); the internal parameter name is a sanitized identifier.
- List operators are annotated `Optional[list[T]]` so repeated/`,`-joined values
  parse natively; range/`between` use a small custom type with a validator.
- The synthesized signature means **OpenAPI/Swagger shows every filter param**,
  grouped and described, with no manual schema writing.

This generation is **memoized per (model, config)** so it runs once, not per
request.

### 4. Parser → FilterAST

At request time the dependency receives the parsed kwargs (already type-coerced)
and builds the neutral AST. The AST is the **contract** between the front half
(HTTP/Pydantic) and the back half (databases).

```python
@dataclass(frozen=True)
class Condition:
    field: str           # source path, e.g. "address.city"
    op: str              # "gte", "contains", "has_all", ...
    value: Any           # already coerced & validated

@dataclass(frozen=True)
class Group:
    op: Literal["and", "or"]
    members: list[Condition | Group]

@dataclass(frozen=True)
class FilterAST:
    where: Group                 # v1: a single top-level AND group
    order_by: list[Sort]         # [(field, ASC|DESC)]
    page: Page                   # offset/limit (or cursor later)
```

The AST is plain data: trivially testable, serializable, loggable. It is the
extension point for OR-groups later (the `Group` node already models it; v1 just
only ever *produces* a top-level AND).

### 5. Backend adapter (compiler)

A `QueryCompiler` turns a `FilterAST` into a backend query. This is the *only*
component that knows about a specific database.

```python
class QueryCompiler(Protocol):
    supported_ops: frozenset[str]            # adapter declares what it can do
    def compile_where(self, group: Group) -> Any: ...
    def compile_order(self, order: list[Sort]) -> Any: ...
    def compile_page(self, page: Page) -> Any: ...
```

- At **registration time**, the chosen adapter's `supported_ops` is intersected
  with the configured operators; an operator the adapter can't compile is rejected
  with a clear error *before* the app serves traffic.
- The Mongo adapter maps `Condition`→Mongo operators (`gte`→`$gte`,
  `contains`→`$regex`, `has_all`→`$all`, …) and merges conditions on the same
  field into one sub-document (`{"age": {"$gte": 21, "$lt": 65}}`).

```python
class MongoCompiler:
    def compile_where(self, group):
        clauses = [self._cond(c) for c in group.members]
        merged = merge_same_field(clauses)
        return merged if group.op == "and" else {"$or": clauses}
```

---

## Why this separation matters

- **Testability:** the front half is tested by asserting on the `FilterAST`; the
  back half is tested by feeding hand-built ASTs and asserting on the query dict.
  No database required for the vast majority of tests.
- **Extensibility:** a new database is a new `QueryCompiler`. The introspector,
  param generator, parser, OpenAPI integration — all unchanged.
- **Honesty:** the adapter *declares* what it supports, so we can never advertise a
  filter param a backend can't fulfill.

---

## The boolean-combination question (AND / OR)

HTTP query strings naturally express **AND of equalities/ranges**. That covers the
overwhelming majority of real list endpoints, so **v1 ships AND-only** and keeps
the surface clean.

We deliberately keep a forward path without committing to it now:

- The AST already has `Group(op="or")`.
- A future, **opt-in** syntax could express OR without turning query strings into a
  DSL, e.g. a repeated bracketed group `?or=(status:active|status:trial)` or a
  small JSON filter body on a `POST /users/search` companion route. We will pick
  one deliberately later, informed by real demand, rather than guessing now.

This is called out as a non-goal in doc 00 precisely so we don't accidentally grow
a query language. The architecture *permits* it; the product *resists* it until
asked.

Continue to **[04 — Backend Roadmap](04-backend-roadmap.md)**.
