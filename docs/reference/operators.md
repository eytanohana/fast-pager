---
icon: lucide/table
---

# Operator Reference

!!! note "The full compound-type surface"
    The current release supports the **scalar types** below, **arrays of
    scalars** (`list[T]` / `set[T]`), **nested Pydantic models** via dotted
    paths, **arrays of nested models** (`list[NestedModel]` via `elem` →
    `$elemMatch`), and **`dict[str, T]` maps** (explicitly enabled, key
    presence + enumerated keys). That completes the type tables of
    [design doc 02](../design/02-type-and-operator-system.md); the
    `FilterSet` class (allow-list filter surfaces per model) lands in
    **`v0.2.0`**.

Every operator set below is the *default* for its type — the **`safe`**
profile. Operators listed under **`full`** are additional operators
available only when a field (or the whole app) opts into the `full` profile;
`fast-pager` never enables them by default because they carry extra cost or
risk (see [Safety](#safety-notes)).

The profiles are only the *default* layer: which operators a specific field
actually exposes can be overridden per type with
`FilterConfig(type_profiles={...})` and per field with
`Annotated[T, Filterable(ops=[...])]` (including `ops.ALL` / `ops.NONE`), or
per route with `FilterConfig(operators={...})` — the full precedence ladder
is in the
[Controlling the filter surface](../tutorial/controlling-fields.md) tutorial.
Whatever the layer, an operator that is not *valid* for the field's type is
rejected at route registration, never at request time.

## Scalar types

| Python / Pydantic type | Default operators (`safe`) | Additional in `full` |
|---|---|---|
| `str` | `eq`, `ne`, `in`, `nin`, `contains`, `startswith`, `endswith` | `icontains`, `istartswith`, `iendswith`, `regex`, `text_search` |
| `int`, `float`, `Decimal` | `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin` | `between` |
| `bool` | `eq` | `ne` |
| `datetime`, `date`, `time` | `eq`, `ne`, `gt`, `gte`, `lt`, `lte` | `between` |
| `UUID` | `eq`, `ne`, `in`, `nin` | |
| `Enum` / `Literal[...]` | `eq`, `ne`, `in`, `nin` | |
| `bytes` | *(not filterable by default)* | `eq` |

`eq` is implicit — a bare `field=value` is treated as `field__eq=value`, so
the common case (`?status=active`) stays clean.

## `Optional[T]` / `T | None`

An optional field exposes everything its wrapped type `T` does, **plus**:

| Operator | Tier | Meaning |
|---|---|---|
| `isnull` | `safe` | `field__isnull=true` / `false` — is the field `null`? |
| `exists` | `full` | Mongo-flavored presence check (`{field: {$exists: bool}}`); SQL adapters alias it to `isnull` semantics or reject it |

`Optional[list[T]]` works the same way: the array operators below, plus
`isnull` / `exists`.

## Arrays of scalars — `list[T]` / `set[T]`

An array field gets operators about **membership and shape** — never the
element type's scalar operators. `tags__contains` would be ambiguous
(substring of an element? membership?), so it does not exist; array fields
expose exactly this family, whatever the element type:

| Operator | Tier | Value | Example | Mongo compilation |
|---|---|---|---|---|
| `has` | `safe` | single element | `tags__has=python` | `{"tags": "python"}` (Mongo matches a scalar against array elements) |
| `has_any` | `safe` | element list | `tags__has_any=python,rust` | `{"tags": {"$in": ["python", "rust"]}}` |
| `has_all` | `safe` | element list | `tags__has_all=python,rust` | `{"tags": {"$all": ["python", "rust"]}}` |
| `len__eq` | `safe` | int | `tags__len__eq=3` | `{"tags": {"$size": 3}}` |
| `len__ne` / `len__gt` / `len__gte` / `len__lt` / `len__lte` | `full` | int | `tags__len__gte=2` | `$expr` over `$size` (see below) |
| `empty` | `safe` | bool | `tags__empty=false` | pinned semantics, see below |

Notes, pinned precisely because arrays are where Mongo surprises people:

- **`empty` semantics** (empty-vs-missing is a classic Mongo trap):
  `empty=true` → the field exists **and** is the empty array —
  `{"tags": {"$eq": []}}`; `empty=false` → the field exists and has at
  least one element — `{"tags.0": {"$exists": true}}`. A **missing** field
  matches *neither*. `empty` reasons only about shape; use `isnull` /
  `exists` on an `Optional[list[T]]` field to reason about presence.
- **`len` comparisons are `full`-tier** because Mongo has no query operator
  for "length ≥ n": they compile to
  `{"$expr": {"$gte": [{"$size": {"$cond": [{"$isArray": "$tags"}, "$tags", []]}}, 2]}}`,
  which cannot use an index (a collection-scan risk — the same reasoning
  that gates other `full` operators). The `$isArray` guard means a missing,
  null, or non-array value counts as **length 0** instead of erroring the
  query. `len__eq` compiles to the plain `$size` query operator (which a
  missing field never matches) and stays `safe`.
- **List values are capped**: the `max_list_length` guard (default 100)
  applies to `has_any` / `has_all` exactly as it does to `in` / `nin`.
- **No bare-equality sugar**: arrays have no `eq` operator, so there is no
  bare `?tags=...` parameter.
- **Arrays are not sortable by default** — sorting on a Mongo array field
  uses min/max element semantics, which surprises people. Opt a field in
  deliberately with `Filterable(sortable=True)` or by naming it in
  `FilterConfig(sortable=[...])`.
- Element-level substring matching (`tags__has_substr`) is designed but not
  shipped yet.

Multi-token names like `tags__len__gte` need no special parsing rules: every
parameter is pre-generated with its exact name at registration, so the full
spelling is simply matched as-is (see the
[filtering tutorial](../tutorial/filtering.md#the-field__op-convention)).

## Nested Pydantic models (embedded documents)

A field whose type is another Pydantic model is walked recursively, and every
supported field inside it becomes filterable by **dotted path** — public
parameter names join the segments with the separator, compiled queries use
Mongo dot notation:

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
?address__city__contains=ams        # {"address.city": {"$regex": "ams"}}
?address__geo__lat__gte=52          # {"address.geo.lat": {"$gte": 52.0}}
?address__city=Amsterdam            # bare eq works on nested leaves too
```

The rules, precisely:

- **Fields inside a nested model get their full normal treatment.** A nested
  scalar exposes its type's scalar operators (including per-type
  `type_profiles` overrides), a nested `list[T]` exposes the
  [array family](#arrays-of-scalars-listt-sett) (`address__tags__has=home`),
  and `Filterable(ops=... / source=... / param=... / sortable=...)` on a
  nested field works exactly as at top level.
- **`source` / `param` / aliases compose per segment.** Each path segment
  resolves its two names independently
  (`param` → alias → field name for the URL; `source` → alias → field name
  for the database), so `Filterable(source="zip")` on `Address.zip_code`
  yields `?address__zip_code=...` → `{"address.zip": ...}`, and a rename on
  the *embedding* field renames that segment for the whole subtree.
- **The embedding field itself has no operators of its own** — no bare
  `?address=` parameter exists — with one exception: an **`Optional`
  embedding** exposes `isnull` (`safe`) and `exists` (`full`), so
  `?billing__isnull=true` finds documents without a billing address. The
  children of a nullable embedding are generated normally; note that when
  the parent is `null`, a condition on a child simply matches nothing —
  combine with `billing__isnull=false` when presence matters.
- **Depth is bounded** by `FilterConfig(max_depth=...)`, default **2**: a
  field's path may cross at most `max_depth` embedded-model boundaries.
  `address__city` (1) and `address__geo__lat` (2) are generated; anything
  deeper is silently skipped. A nested model sitting exactly at the bound
  keeps its own `isnull`/`exists` (when nullable) but none of its children.
  The bound is what keeps the parameter surface finite — and it is also the
  cycle guard: self-referential or mutually-recursive models simply truncate
  at the bound.
- **Subtree opt-out:** `Filterable(ops=ops.NONE)` on the embedding field
  removes the field *and every descendant* from the filter surface. Other
  `Filterable` options on an embedding field never touch the children —
  `ops=[...]` is validated against the embedding's own operator set
  (`isnull`/`exists` when nullable, nothing otherwise). Route-level,
  `FilterConfig(exclude=["billing"])` excludes the subtree, and
  `exclude=["address__city"]` a single nested field.
- **Config keys use the public dotted-param spelling**:
  `FilterConfig(operators={"address__city": ["contains"]})`,
  `sortable=["address__city"]`, `exclude=["address__city"]`.
- **Nested leaves sort by their public name** — `?sort=-address__city`
  compiles to the dotted source `address.city`. Scalar leaves are sortable
  by default (sortable-iff-filterable, as at top level); embedding fields
  and nested arrays are not.
- **Name collisions are impossible to mis-parse and loud to misconfigure**:
  because matching is exact, a literal field named `address__city` and a
  nested `address.city` path that generate the same parameter name raise a
  `ConfigurationError` at registration naming both sources.

## Arrays of nested models — `list[NestedModel]`

A field whose type is a *list of* Pydantic models gets element matching via
the **`elem`** path segment:

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

compiles to a **single `$elemMatch`**:

```python
{"orders": {"$elemMatch": {"amount": {"$gte": 100.0}, "status": "refunded"}}}
```

!!! warning "Same-element vs. independent conditions — the `$elemMatch` surprise"
    This is the whole point of the `elem` token, and it is where Mongo
    surprises people most. **All conditions on the same `orders__elem__...`
    prefix in one request must hold for the *same* array element** — the
    query above finds users with *one order* that is both ≥ 100 **and**
    refunded.

    Mongo's *default* array-matching semantics are the opposite:
    `{"orders.amount": {"$gte": 100}, "orders.status": "refunded"}` matches
    a user whose ≥-100 order and refunded order are **different elements**.
    `fast-pager` deliberately does **not** generate those independent
    dotted-path parameters for arrays of models — if a request names an
    `elem` path, you get same-element semantics, always. (Independent
    conditions across *different* array fields remain independent: each
    array field gets its own `$elemMatch`.)

The rules, precisely:

- **`elem` parameters are `full`-tier as a whole** — precisely because of
  the subtlety above (design doc 02). Under the default `safe` profile no
  `elem` parameters are generated; opt in with
  `FilterConfig(default_profile="full")`, with `Filterable(ops=[...])` on
  the element model's field, or with a `FilterConfig(operators=
  {"orders__elem__amount": [...]})` entry naming the `elem` path.
- **Fields inside the element get their normal operator surface** (scalar
  operators, the array family for a `list[T]` inside the element,
  `Filterable` renames — `source=` composes *relatively*: inside
  `$elemMatch`, keys are relative to the element). One exception:
  `text_search` is collection-level and never applies inside elements —
  explicitly configuring it on an `elem` path is a `ConfigurationError`.
- **The array field itself gets the shape operators** — `len__eq` / `empty`
  (`safe`) and the `len` comparisons (`full`), compiled exactly as for
  [arrays of scalars](#arrays-of-scalars-listt-sett) — plus
  `isnull`/`exists` when `Optional`. It does **not** get
  `has`/`has_any`/`has_all` (element equality against whole documents is
  not expressible through typed query parameters), and there is no bare
  `?orders=` parameter. A shape condition and an `elem` group on the same
  field merge: `{"orders": {"$size": 2, "$elemMatch": {...}}}`.
- **The `elem` hop counts as one `max_depth` boundary**, exactly like an
  embedding: with the default `max_depth=2`, `orders__elem__amount` (1) and
  `orders__elem__items__elem__qty` (2, a nested array inside the element)
  are generated. Cycles truncate at the bound, as for embeddings.
- **`elem` paths are never sortable.** "Sort users by the amount of an
  order" has no per-document meaning; naming an `elem` path in
  `FilterConfig(sortable=[...])` is a `ConfigurationError`, and
  `Filterable(sortable=True)` on an element model's field is ignored for
  its `elem` uses.
- **Subtree opt-out works as for embeddings**: `Filterable(ops=ops.NONE)`
  on the `list[NestedModel]` field (or `FilterConfig(exclude=["orders"])`)
  removes the field and every `elem` descendant;
  `exclude=["orders__elem__amount"]` removes a single element field.

## Maps — `dict[str, T]`

Free-form maps clash with a core promise — every parameter is
pre-generated, typed, and documented in OpenAPI — so support is
deliberately narrow, and **maps are not filterable by default**: a plain
`dict[str, T]` field generates nothing. A `Filterable` annotation enables
it:

```python
class User(BaseModel):
    metadata: Annotated[dict[str, str], Filterable(keys=["region", "tier"])]
    counters: Annotated[dict[str, int], Filterable()]
    attrs: dict[str, str]          # no annotation → not filterable
```

| Parameter | Enabled by | Value | Mongo compilation |
|---|---|---|---|
| `metadata__has_key=region` | any `Filterable(...)` on the field | the key name (`str`, whatever `T` is) | `{"metadata.region": {"$exists": true}}` |
| `metadata__region=emea` | only keys enumerated in `Filterable(keys=[...])` | typed as `T`, implicit `eq` only | `{"metadata.region": "emea"}` |

- **`has_key` values are sanitized.** The key is user input inserted into a
  backend field path, so keys that are empty or contain `.`, `$`, or null
  bytes are rejected with a standard 422 (and the Mongo compiler re-checks
  and raises for direct AST users — defense in depth). The same rule
  applies, at registration time, to the keys listed in
  `Filterable(keys=[...])`.
- **Value-at-key parameters exist only for enumerated keys** — there is no
  request-time `metadata__<anything>`; un-enumerated spellings are unknown
  parameters (a 422 in strict mode). Each enumerated key gets a typed,
  `eq`-only parameter (`metadata__region` / `metadata__region__eq`);
  configuring any other operator on it is a `ConfigurationError`. Richer
  per-key operator sets may come with `FilterSet`.
- **Type constraints are enforced at registration**: `Filterable` on a bare
  `dict`, a non-`str` key type, or an unsupported value type raises
  `ConfigurationError`, as does `Filterable(keys=[...])` on a non-map field.
- **Maps are not sortable by default** (and expose no bare `?metadata=`
  parameter). An enumerated key path may be opted into sorting via
  `FilterConfig(sortable=["metadata__region"])` — it compiles to the dotted
  `metadata.region`. `Optional[dict[str, T]]` adds `isnull` (`safe`) and
  `exists` (`full`).

## Operator semantics

| Operator | Value arity | Example | Mongo compilation |
|---|---|---|---|
| `eq` | single | `age__eq=21` (or bare `age=21`) | `{"age": 21}` |
| `ne` | single | `age__ne=21` | `{"age": {"$ne": 21}}` |
| `gt` / `gte` / `lt` / `lte` | single | `age__gte=21` | `{"age": {"$gte": 21}}` |
| `between` | range (2 values) | `age__between=21,65` | sugar for `gte` + `lte` |
| `in` / `nin` | list | `status__in=a,b` or repeated `status__in=a&status__in=b` | `{"status": {"$in": ["a", "b"]}}` |
| `contains` | single | `name__contains=ana` | `{"name": {"$regex": "ana"}}` (value `re.escape()`d) |
| `icontains` | single | `name__icontains=ANA` | as `contains`, case-insensitive |
| `startswith` / `endswith` | single | `name__startswith=al` | anchored, escaped `$regex` |
| `regex` | single | `name__regex=^a.*z$` | raw `$regex` — `full`-tier, gated by config, disabled by default |
| `text_search` | single | `name__text_search=alice` | Mongo `$text` over a text index (requires backend capability) |
| `isnull` | bool | `email__isnull=true` | `{"email": null}` |
| `exists` | bool | `email__exists=false` | `{"email": {"$exists": false}}` |
| `has_key` | single (the key name) | `metadata__has_key=region` | `{"metadata.region": {"$exists": true}}` — key sanitized, see [Maps](#maps-dictstr-t) |

List-value operators (`in`, `nin`, and the array operators `has_any` /
`has_all`) accept both comma-joined values and repeated query keys; each
element is coerced to the field's (element) type. A `max_list_length` guard
(default 100) caps how many values are accepted.

## Safety notes

- **`regex` is `full`-only** and gated by an additional config flag — full
  pattern matching is a ReDoS and collection-scan risk.
- **`contains` / `icontains` values are always `re.escape()`d** before being
  compiled, so user input is a literal substring, never a pattern — `contains`
  itself can never become a ReDoS vector. It still compiles to an unanchored
  `$regex`, so it can still trigger a collection scan on an unindexed field;
  reach for `text_search` when that matters.
- **Case-insensitive variants (`i*`) are separate, explicit operators**
  rather than a flag, so they show up by name in the generated docs and
  compile to explicit backend forms.

Full detail, including the rationale for each default, lives in
[design doc 02 — Type & Operator System](../design/02-type-and-operator-system.md).
