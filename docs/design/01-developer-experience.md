# 01 — Developer Experience

The single most important design decision is *how the user wires this up*. The
type system and the Mongo translation are "merely" engineering; the API surface is
the product. This document explores the realistic options, weighs them, and lands
on a recommendation that supports **progressive disclosure** — trivial things are
trivial, complex things are possible.

## The naming convention: `field__op`

We use Django's double-underscore convention: `<field>__<operator>=<value>`.

- `name__contains=ana`
- `age__gte=21`
- `created_at__lt=2025-01-01`

Why double underscore and not single (`age_gt`)?

- Single underscore is **ambiguous** the moment a field is named `created_at` or
  `is_active` — is `created_at_gte` the field `created` with op `at_gte`? Double
  underscore as a *separator token* removes most of that ambiguity.
- It is the de-facto Python convention (Django ORM, MongoEngine, django-filter),
  so it reads as "filtering" to the audience immediately.

Importantly, `__` is a *naming convention*, **not a request-time parsing rule**.
Every legal parameter name is generated ahead of time from the model, so an
incoming parameter is matched **exactly** against that pre-generated set — the
library never has to guess how to split a string (see doc 02, *Parameter
matching*). This sidesteps edge cases like field names that themselves contain
`__` or nested fields that happen to share a name with an operator.

Bare equality keeps the natural form: `name=alice` is sugar for `name__eq=alice`.

> Some teams prefer single-underscore spellings (`age_gt`, `age_le`). We
> deliberately recommend `__` for the reasons above, but the separator is a
> **global config knob** (`FilterConfig(separator="__")`) for teams who insist —
> safe precisely because parameters are matched against a pre-generated set,
> not parsed.

Operator spellings follow Mongo/Django for muscle memory: `gte`, `lte`, `gt`,
`lt`, `ne`, `in`, `nin`, `contains`, `startswith`, `regex`, … (full table in doc 02).

---

## Option A — Inline `Annotated` metadata on the model

Declare filterability *where the field is declared*.

```python
from fast_pager import Filterable, ops

class User(BaseModel):
    name: Annotated[str, Filterable(ops["contains", "startswith", "eq"])]
    age:  Annotated[int, Filterable(ops.ALL)]
    ssn:  str  # no Filterable → not filterable at all
```

```python
@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

**Pros**
- Single source of truth; the model literally documents its own filterability.
- Reads beautifully; great for models owned by the same team.

**Cons**
- Couples the domain/serialization model to an HTTP concern. The same `User`
  model may be used in contexts where filtering is irrelevant.
- You can't expose *different* filtering on the same model for two endpoints
  (admin vs public) without a second model.

**Verdict:** Excellent for the common case; offer it, but don't make it the *only*
way.

---

## Option B — External `FilterSet` (django-filter style)

Keep the model clean; declare filtering separately.

```python
from fast_pager import FilterSet

class UserFilter(FilterSet):
    class Meta:
        model  = User
        fields = {
            "name": ["contains", "startswith"],
            "age":  ["gte", "lte"],
            # fields omitted here are NOT filterable
        }

@app.get("/users")
async def list_users(q: UserFilter = FilterDepends(UserFilter)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

**Pros**
- Total decoupling: model stays a pure data model.
- Multiple filtersets per model (public/admin) trivially.
- Natural home for cross-field, computed, or custom filters later.

**Cons**
- A second class to maintain; more ceremony for the simple case.

**Verdict:** The "pro" tier. This is where power users and large teams live.

---

## Option C — Zero-config, derive everything

No annotations, no filterset. Infer safe defaults purely from field types.

```python
@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    ...
```

`str` fields get string operators, numerics get comparison operators, etc., using
a default operator profile per type (doc 02). Expensive operators (`regex`) are
**off** by default.

**Pros**
- The lowest-friction possible onboarding: literally one dependency.
- Great for prototyping and internal tools.

**Cons**
- Exposes *every* field, including ones you'd rather keep unfilterable (`password_hash`).
  Mitigated by a default deny-list of types and an `exclude=` option, but the
  ergonomic answer is "graduate to Option A or B when you care."

**Verdict:** Ship it as the default behavior of `FilterQuery[Model]` with safe
profiles, and make graduating to A/B additive (no rewrite).

---

## The unifying mechanism: `FilterDepends(...)`

All three options resolve to the same runtime object via a FastAPI dependency.
The dependency is what makes parameters appear in `/docs` (see doc 03).

**Why the model/filterset is passed explicitly** — `FilterDepends(User)`, not
`FilterDepends()`: FastAPI resolves a dependency from the callable in the
parameter's *default value* (or in `Depends(...)` inside `Annotated`), and that
callable never sees the parameter's type annotation. A bare `FilterDepends()`
therefore has no way to discover the model. The annotation
(`FilterQuery[User]`) still matters — it types the object for your editor and
mypy — but the source of truth for generation is the argument. The
`Annotated` idiom works too, and avoids stating the model twice:

```python
UserQuery = Annotated[FilterQuery[User], FilterDepends(User)]

@app.get("/users")
async def list_users(q: UserQuery):
    ...
```

`FilterDepends` validates at registration time that the annotation and the
argument agree, so the two can never silently drift.

The returned object is uniform regardless of how it was declared:

```python
q.to_mongo()        # -> dict ready for pymongo/motor .find()
q.sort_mongo()      # -> list[tuple[str, int]] for .sort()
q.skip, q.limit     # -> ints for pagination
q.to_ast()          # -> backend-agnostic FilterAST (for custom adapters/testing)
q.applied           # -> the parsed, validated filters (introspectable)
```

This uniformity is the key to **progressive disclosure**: a team can start with
Option C, sprinkle `Annotated` (Option A), and later extract a `FilterSet`
(Option B) — and **no call site changes**, because every path yields the same `q`.

---

## Pagination & sorting (first-class, per the name)

These ride on the same dependency and are configurable globally and per-route.

```
GET /users?sort=-age,name&limit=20&offset=40
```

- **sort**: comma-separated field list; `-` prefix = descending. Only fields the
  filterset/model marks `sortable` are accepted (default: same as filterable).
  Compiles to `[("age", -1), ("name", 1)]`.
- **pagination strategy** is pluggable:
  - `offset` / `limit` (default; maps to Mongo `skip`/`limit`).
  - `page` / `page_size` (sugar over offset).
  - `cursor` (keyset pagination over a sort key) — phase 3; far better for deep
    pages and large collections, but needs an opaque cursor token. Designed for,
    not shipped in v1. Keyset pagination requires a **total order**: the library
    appends a unique tiebreaker (`_id` for Mongo, the primary key for SQL) to
    the user's sort key automatically, otherwise rows with equal sort values
    would be skipped or duplicated across pages.

Each strategy has guardrails: `max_limit`, `default_limit`. (See doc 02 safety.)

---

## Response envelope (optional, opt-in)

By default we return *only* the query; you shape the response. But a common need
is a paginated envelope with a total count. We offer an opt-in helper:

```python
@app.get("/users", response_model=Page[User])
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await q.paginate(db.users)   # runs find + count, returns Page[User]
```

```json
{
  "items": [ ... ],
  "total": 137,
  "limit": 20,
  "offset": 40
}
```

`Page[T]` is a generic Pydantic model so `response_model` and the OpenAPI schema
stay correct. This is a convenience, never a requirement — `to_mongo()` always
remains available for full control.

**The count is not free.** An exact `total` runs `count_documents` (or
`SELECT COUNT(*)`) with the same filter on every page request, which is
expensive on large collections. So `total` is `Optional[int]` in `Page[T]` and
the cost is a knob: `paginate(..., total="exact" | "estimated" | "none")` —
`estimated` uses `estimatedDocumentCount` where the backend offers it (only
valid for unfiltered queries; the library falls back to `exact` or `none` and
says so), and `none` skips the count entirely (the right default for
infinite-scroll UIs and a natural fit with cursor pagination).

---

## Errors and ergonomics

- **Config-time errors** (operator not valid for type, unknown field in a
  `FilterSet`, bad separator) raise at import/registration with a precise message
  naming the field and operator. You learn at boot, not in prod.
- **Request-time errors** (a client sends `age__gte=banana`) return a normal
  FastAPI **422** with the standard validation error shape, because each parameter
  is a properly typed `Query(...)`. No custom error format to learn.
- **Unknown operators/params**: configurable — `ignore` (default, forgiving) or
  `strict` (422 on unrecognized `field__op`). Strict mode is great for catching
  client typos in development.

---

## The DX we recommend

1. **Default path:** `FilterQuery[Model]` with zero config (Option C) and safe
   per-type operator profiles. One import, one dependency.
2. **Tighten inline** with `Annotated[T, Filterable(...)]` (Option A) when you want
   to curate operators or hide fields without leaving the model.
3. **Graduate to `FilterSet`** (Option B) for decoupling, multiple views per model,
   and custom/computed filters.

All three share `FilterDepends(...)` and the uniform `q` object. Moving up the
ladder only changes the argument (`User` → `UserFilter`) — that property is the
heart of the DX.

Continue to **[02 — Type & Operator System](02-type-and-operator-system.md)**.
