---
icon: lucide/table
---

# Operator Reference

!!! note "Scalars, arrays of scalars, and nested models (for now)"
    The current release supports the **scalar types** below, **arrays of
    scalars** (`list[T]` / `set[T]`), and **nested Pydantic models** via
    dotted paths. The remaining compound types are designed (see
    [design doc 02](../design/02-type-and-operator-system.md)) and land in
    phased releases: arrays of nested models and `dict[str, T]` keys in
    **`v0.1.3`**, and `FilterSet` in **`v0.2.0`**. This page grows a table
    per type as each ships.

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
- `list[NestedModel]` element matching (`elem` → `$elemMatch`) ships in
  `v0.1.3`.

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
