---
icon: lucide/list-filter
---

# Filtering

## The `field__op` convention

`fast-pager` uses Django's double-underscore convention to name query
parameters: `<field>__<operator>=<value>`.

```text
name__contains=ana
age__gte=21
created_at__lt=2025-01-01
```

Why double underscore and not single (`age_gt`)? Single underscore becomes
ambiguous the moment a field is named `created_at` or `is_active` — is
`created_at_gte` the field `created` with operator `at_gte`, or the field
`created_at` with operator `gte`? `__` as a dedicated separator token removes
that ambiguity, and it's the convention Django, MongoEngine and
`django-filter` users already know.

Bare equality keeps the natural form: `name=alice` is sugar for
`name__eq=alice`.

!!! info "This is a naming convention, not a parser"
    `fast-pager` never splits an incoming parameter name at request time.
    Every legal `field__op` parameter is generated **once**, from the model,
    when the route is registered — and an incoming request parameter is
    matched *exactly* against that pre-generated set. This is what makes
    nested paths (`address__city__contains`) and multi-token operators
    (`tags__len__gte`) unambiguous: the full spelling is simply the name of
    a parameter that was generated ahead of time, not something parsed on
    the fly. Field names that happen to contain `__`, or that collide with
    another generated name, are caught as configuration errors at
    registration — never mis-parsed at runtime.

    Prefer single-underscore spellings? The separator is a global config
    knob (`FilterConfig(separator="_")`) — safe precisely because of the
    pre-generation behavior above.

## Worked example

```python
class User(BaseModel):
    name: str
    age: int

@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

A request:

```text
GET /users?name__contains=ana&age__gte=21&age__lt=65
```

compiles to:

```python
{"name": {"$regex": "ana"}, "age": {"$gte": 21, "$lt": 65}}
```

Conditions on different fields are combined with `AND`. `fast-pager`
deliberately does not support arbitrary boolean nesting
(`(a OR b) AND c`) over query strings in v1 — see
[design doc 00](../design/00-overview.md#non-goals-at-least-for-10) for why.

## Operators, by category

The exact operator set depends on the field's type (full tables in the
[Operator Reference](../reference/operators.md)), but they fall into a few
families:

- **Equality & membership** — `eq` (implicit), `ne`, `in`, `nin`. Available
  on every filterable type.
- **Ordering** — `gt`, `gte`, `lt`, `lte`, and the `between` sugar
  (`age__between=21,65` for `age__gte=21&age__lte=65`). Numeric and
  date/time types only.
- **String matching** — `contains`, `startswith`, `endswith` (safe tier);
  `icontains`, `istartswith`, `iendswith`, `regex`, `text_search` (full
  tier, opt-in).
- **Presence** — `isnull` (and Mongo's `exists`) on `Optional[T]` fields.

```text
?status__in=active,pending          # membership, comma-joined or repeated keys
?status__in=active&status__in=pending
?age__between=21,65                 # sugar for age__gte=21&age__lte=65
?email__isnull=false                # Optional[str] field is present
```

List-valued operators (`in`, `nin`) accept **both** comma-joined values and
repeated keys, and each element is coerced to the field's type — so
`age__in=abc` still returns a clean `422`, the same as any other bad value.

## Array fields

`list[T]` and `set[T]` fields (any supported scalar element type) get their
own operator family, about **membership and shape**:

```python
class User(BaseModel):
    name: str
    tags: list[str]
```

```text
?tags__has=python                   # array contains this element
?tags__has_any=python,rust          # any-of (comma-joined or repeated keys)
?tags__has_all=python,rust          # all-of
?tags__len__eq=3                    # array length
?tags__len__gte=2                   # length comparisons (full tier, opt-in)
?tags__empty=false                  # non-empty array
```

`?tags__has=python&tags__len__eq=3` compiles to:

```python
{"tags": {"$eq": "python", "$size": 3}}
```

Array fields deliberately do **not** get the element type's scalar
operators — `tags__contains` would be ambiguous (substring of an element,
or membership?), so it simply doesn't exist. Each `has_*` value is coerced
to the element type, and the same `max_list_length` guard that protects
`in`/`nin` caps `has_any`/`has_all`.

Two Mongo traps are pinned down for you (details in the
[Operator Reference](../reference/operators.md#arrays-of-scalars-listt-sett)):
`empty` distinguishes *empty* from *missing* — a missing field matches
neither `empty=true` nor `empty=false` — and array fields are **not
sortable by default** (opt in per field with `Filterable(sortable=True)`).

## Nested models

Fields whose type is another Pydantic model are filterable by **dotted
path**: the public parameter joins the segments with `__`, the compiled
query uses Mongo dot notation.

```python
class Geo(BaseModel):
    lat: float
    lon: float

class Address(BaseModel):
    city: str
    tags: list[str]
    geo: Geo

class User(BaseModel):
    name: str
    address: Address
    billing: Optional[Address] = None
```

```text
?address__city__contains=ams        # nested string field
?address__city=Amsterdam            # bare eq works on nested leaves too
?address__tags__has=home            # nested arrays get the array family
?address__geo__lat__gte=52          # two levels deep
?billing__isnull=true               # Optional embedding: presence check
?sort=-address__city                # nested leaves sort by public name
```

A request like `?address__geo__lat__gte=52&address__geo__lat__lt=53`
compiles to one merged sub-document, exactly like a flat field:

```python
{"address.geo.lat": {"$gte": 52.0, "$lt": 53.0}}
```

Everything you know from flat fields carries over: nested scalars get their
type's operators (and `type_profiles` overrides), nested `list[T]` fields
get the array family, `Filterable(...)` on a nested field works unchanged,
and `source=`/`param=`/aliases rename *their own segment* of the dotted
path. `FilterConfig` keys use the public dotted spelling —
`operators={"address__city": ["contains"]}`, `exclude=["address__city"]`,
`sortable=["address__city"]`.

Three things are specific to nesting:

- **Depth bound.** Generation descends `FilterConfig(max_depth=...)`
  embedded-model levels below the root (default **2**). Deeper fields are
  silently skipped — this keeps the parameter surface and your OpenAPI docs
  finite, and it is also what makes self-referential models safe.
- **The embedding field itself.** `?address=` doesn't exist — you filter
  the leaves, not the subdocument. The exception: an `Optional` embedding
  exposes `isnull`/`exists`, because "has no billing address" is a real
  question. Children of a nullable embedding behave normally (a condition
  on `billing__city` simply matches nothing when `billing` is null).
- **Excluding a subtree.** `Filterable(ops=ops.NONE)` on the embedding
  field removes the whole subtree from the filter surface, as does
  `FilterConfig(exclude=["billing"])` at the route level.

Full rules — segment naming, collision detection, embedding-field
`Filterable` semantics — are in the
[Operator Reference](../reference/operators.md#nested-pydantic-models-embedded-documents).

## Arrays of nested models

A `list[NestedModel]` field gets **element matching** via the `elem` path
segment:

```python
class Order(BaseModel):
    amount: float
    status: Literal["paid", "refunded"]

class User(BaseModel):
    name: str
    orders: list[Order]
```

```text
?orders__elem__amount__gte=100&orders__elem__status=refunded
```

compiles to a **single `$elemMatch`** — both conditions must hold for the
**same order**:

```python
{"orders": {"$elemMatch": {"amount": {"$gte": 100.0}, "status": "refunded"}}}
```

That same-element guarantee is the whole point, and it is the opposite of
what raw Mongo dotted paths do (`{"orders.amount": ..., "orders.status":
...}` lets *different* elements satisfy each condition — a classic
surprise). Every condition sharing an `orders__elem__` prefix in one
request joins the same `$elemMatch`; distinct array fields each get their
own.

Because same-element semantics are subtle, **`elem` parameters are
full-tier**: they are generated only under
`FilterConfig(default_profile="full")` or an explicit per-field
(`Filterable(ops=[...])` on the element model's field) or per-route
(`FilterConfig(operators={"orders__elem__amount": [...]})`) opt-in. The
array field itself keeps the safe-tier **shape** operators
(`orders__len__eq=2`, `orders__empty=false`) but no membership family, and
`elem` paths are **never sortable**. The `elem` hop counts as one
`max_depth` level, like an embedding. Full rules in the
[Operator Reference](../reference/operators.md#arrays-of-nested-models-listnestedmodel).

## Maps

`dict[str, T]` fields are **not filterable by default** — a free-form key
set can't be pre-generated, typed, and documented. A `Filterable`
annotation enables the narrow, safe surface:

```python
class User(BaseModel):
    metadata: Annotated[dict[str, str], Filterable(keys=["region", "tier"])]
    counters: Annotated[dict[str, int], Filterable()]
    attrs: dict[str, str]               # stays unfilterable
```

```text
?metadata__has_key=region            # {"metadata.region": {"$exists": true}}
?metadata__region=emea               # {"metadata.region": "emea"} (typed, eq only)
?counters__has_key=visits            # any Filterable() enables has_key
```

`has_key` is always available on an enabled map; typed value-at-key
parameters exist **only** for the keys enumerated in
`Filterable(keys=[...])` — `metadata__plan=pro` is simply an unknown
parameter. Because a `has_key` value is inserted into a backend field path,
keys containing `.`, `$`, or null bytes are rejected with a 422. Details in
the [Operator Reference](../reference/operators.md#maps-dictstr-t).

## Safety by default

Some operators are gated because they're expensive or dangerous on an
unindexed collection:

- **`regex` is off by default.** It's a full pattern-match operator with
  ReDoS and full-collection-scan risk. Turn it on per-field or globally with
  eyes open.
- **`contains` / `icontains` are always `re.escape()`d.** The value you send
  is matched as a literal substring, never as a regex pattern — so
  `contains` itself is never a ReDoS vector. It still compiles to an
  unanchored Mongo `$regex`, so it can still scan an unindexed collection;
  use the opt-in `text_search` operator over a real text index when that
  matters.
- **`max_list_length`** caps how many values `in`/`nin` accept, so a request
  can't blow up an `$in` clause.

These are `FilterConfig` knobs with safe defaults — see
[design doc 02](../design/02-type-and-operator-system.md#safety-performance-defaults-that-protect-users)
for the full list.

## Next

Continue to [Sorting & Pagination](sorting-pagination.md), or jump straight
to the [Operator Reference](../reference/operators.md) for the full type ×
operator tables.
