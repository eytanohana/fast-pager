# 02 — Type & Operator System

This is the technical heart of the product: **which Python/Pydantic types we
support, which operators each exposes, how compound types behave, and how a user
controls all of it.**

## Mental model

Two registries drive everything:

1. **Type → operator profile.** Each supported type has a *default profile*: the
   set of operators it exposes out of the box. Profiles are tiered (`safe`,
   `full`) so we can keep dangerous operators off by default.
2. **Operator → semantics.** Each operator defines its value arity (single /
   list / range), the value's relationship to the field type (same type / bool /
   int), and how each backend adapter compiles it.

Resolving a model field is then: `field type → profile → operator set`, with
per-field overrides layered on top.

---

## Scalar types

| Python / Pydantic type | Default operators (`safe`) | Additional in `full` |
|---|---|---|
| `str` | `eq`, `ne`, `in`, `nin`, `contains`, `startswith`, `endswith` | `icontains`, `istartswith`, `iendswith`, `regex` |
| `int`, `float`, `Decimal` | `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin` | `between` |
| `bool` | `eq` | `ne` |
| `datetime`, `date`, `time` | `eq`, `ne`, `gt`, `gte`, `lt`, `lte` | `between` |
| `UUID` | `eq`, `ne`, `in`, `nin` | |
| `Enum` / `Literal[...]` | `eq`, `ne`, `in`, `nin` | |
| `bytes` | *(not filterable by default)* | `eq` |

Notes:

- **`regex` is `full`-only** and additionally gated by a config flag, because it
  is a ReDoS and full-scan risk. See *Safety* below.
- **Case-insensitive variants** (`i*`) are separate operators rather than a flag,
  so they appear explicitly in the docs and compile to explicit backend forms.
- **`eq` is implicit**: a bare `field=` is treated as `field__eq=`. This keeps the
  90% case (`?status=active`) clean.

---

## Optionals and nullability

`Optional[T]` / `T | None` exposes everything `T` does **plus**:

- `isnull` → `field__isnull=true|false` (compiles to `{field: None}` /
  `{field: {$ne: None}}`, or `IS NULL` in SQL).
- `exists` (Mongo-flavored; `full` only) → `{field: {$exists: bool}}`. For SQL
  adapters `exists` aliases to `isnull` semantics or is rejected — adapters
  declare which operators they support (doc 03/04).

---

## Compound types

This is where the design earns its keep. The user explicitly called out
`list[str]`; here is the full treatment.

### `list[T]` / `set[T]` (arrays of scalars)

An array field needs operators about **membership and shape**, which are distinct
from scalar operators. We expose a curated set:

| Operator | Meaning | Mongo compilation |
|---|---|---|
| `has` | array contains this element | `{tags: "x"}` (Mongo matches scalar against array) |
| `has_any` | contains any of these | `{tags: {$in: [...]}}` |
| `has_all` | contains all of these | `{tags: {$all: [...]}}` |
| `len` (`len__gte`, etc.) | array length comparison | `{tags: {$size: n}}` / `$expr` for ranges |
| `empty` | is empty / non-empty | `{tags: {$size: 0}}` / `{$exists,$not}` |

Query forms:

```
?tags__has=python
?tags__has_any=python,rust            # any-of
?tags__has_all=python,rust            # all-of
?tags__len__gte=2
?tags__empty=false
```

> Design choice: we do **not** silently apply scalar string operators
> (`contains`) to `list[str]` — `tags__contains` would be ambiguous (substring of
> an element? membership?). Array fields get array operators. Element-level
> substring matching is an explicit, named, `full`-tier operator
> (`tags__has_substr`) so the intent is unmistakable.

### Nested Pydantic models (embedded documents)

```python
class Address(BaseModel):
    city: str
    zip: str

class User(BaseModel):
    address: Address
```

Nested fields are reachable by **dotted path**, which maps cleanly to Mongo's dot
notation:

```
?address__city__contains=ams      # field path = address.city, op = contains
```

Parsing rule (precise): split the parameter on `__`; the **last segment that is a
known operator** is the operator, everything before it is a field path; join the
path segments with `.` for Mongo. If the last segment is *not* a known operator,
treat the whole thing as a field path with implicit `eq` (so `address__city`
means `address.city == value`).

- **Recursion depth is bounded** (default 2 levels) and configurable, to keep the
  generated parameter surface finite and the docs readable.
- Cycles (self-referential models) are detected and truncated at the depth limit.

### `list[NestedModel]` (arrays of embedded documents)

```python
class User(BaseModel):
    orders: list[Order]
```

These need **element-match** semantics ("a user with an order over $100 that is
also refunded"). Mongo expresses this with `$elemMatch`. We expose it explicitly:

```
?orders__elem__amount__gte=100&orders__elem__status=refunded
```

The `elem` token groups conditions that must hold for the *same* array element,
compiling to a single `$elemMatch`. Without `elem`, conditions on different
parameters are independent (Mongo's default array-matching semantics) — we
document this difference loudly because it surprises people. v1 may ship
`elem` as `full`-tier given the subtlety.

### `dict[str, T]` / free-form maps

Limited support: key-presence and value access by known key.

```
?metadata__has_key=region
?metadata__region=eu             # value at key 'region'
```

Arbitrary-key value filtering is otherwise out of scope (it's effectively a
schemaless escape hatch). Default: **not filterable** unless explicitly enabled.

### `Union[A, B]` (non-optional unions)

Discouraged for filtering — the operator set is ambiguous. Default: not
filterable; the library logs a one-time warning naming the field and how to make
it explicit (annotate it, or pick a concrete type via `FilterSet`).

---

## Field → DB-name mapping and aliases

- **Pydantic aliases** are respected: if a field has `alias="userName"`, the
  query parameter uses the public name; the compiled query uses the source name.
- **Explicit source override** for when the Mongo field differs from the model:

  ```python
  age: Annotated[int, Filterable(source="ageYears")]
  ```

  `?age__gte=21` → `{"ageYears": {"$gte": 21}}`.

- **Custom parameter name** (decouple URL from field):

  ```python
  age: Annotated[int, Filterable(param="minimum_age", ops=["gte"])]
  ```

---

## Per-field configurability — the core question you asked

**Yes — it must be configurable which operators are exposed per field**, and the
design provides four layers, from coarse to fine, each overriding the previous:

1. **Global default profile.** `FilterConfig(default_profile="safe")` — applies to
   every field by type. This is the zero-config behavior.

2. **Per-type override.** "All strings in this app expose `icontains` but never
   `regex`."

   ```python
   FilterConfig(type_profiles={str: ["eq", "contains", "icontains"]})
   ```

3. **Per-field inline (`Annotated`).** Wins over type-level.

   ```python
   email: Annotated[str, Filterable(ops=["eq"])]           # exact-match only
   bio:   Annotated[str, Filterable(ops=ops.NONE)]         # explicitly unfilterable
   score: Annotated[int, Filterable(ops=ops.ALL)]          # everything int supports
   ```

4. **Per-field in a `FilterSet`.** Wins over everything; also where you put
   custom/computed filters.

   ```python
   class UserFilter(FilterSet):
       class Meta:
           model = User
           fields = {"name": ["contains"], "age": ["gte", "lte"]}
       # custom filter not derivable from a single field:
       active_since = DateFilter(field="last_login", op="gte")
   ```

### Allow-list vs deny-list semantics

- In **Option C (zero-config)** the model is an implicit *allow-list by type*:
  filterable types in, sensitive types (`bytes`, `dict`, bare `Union`) out by
  default, plus a global `exclude=[...]` for named fields.
- In **`FilterSet`** the `fields` mapping is a strict allow-list: **if it's not
  listed, it's not filterable.** This is the safe default for public APIs — you
  opt fields *in*, never accidentally leak a new field by adding it to the model.

This allow-list-by-default-in-FilterSet property is a deliberate security
posture, not an accident.

---

## Operator value parsing

- **Single-value ops** (`eq`, `gte`, `contains`): the value is coerced to the
  field's type by Pydantic (so `age__gte=21` yields an `int`, and `age__gte=xx`
  yields a clean 422).
- **List-value ops** (`in`, `nin`, `has_any`, `has_all`): accept **both**
  - repeated keys: `?status__in=a&status__in=b`, and
  - comma-joined: `?status__in=a,b`

  Each element is coerced to the field type. A `max_list_length` guard applies
  (default 100) to prevent giant `$in` clauses.
- **Range ops** (`between`): two values, `?age__between=21,65` → `{$gte:21,$lte:65}`.
  Sugar over `gte`+`lte`; we keep both spellings, `between` is just nicer to read.
- **Bool-valued ops** (`isnull`, `empty`, `exists`): parse `true/false/1/0`.

---

## Safety & performance (defaults that protect users)

Filtering APIs are an unbounded attack/footgun surface. Defaults are conservative:

- **`regex` off by default** (ReDoS + collection-scan risk). Enable per-field or
  globally with eyes open; when enabled we anchor/length-cap patterns and document
  the risk.
- **`contains`/`icontains` compile to unanchored regex in Mongo** → collection
  scans on unindexed fields. We expose this honestly in docs and offer a
  `text_search` operator that uses a Mongo text index where one exists.
- **`max_list_length`** caps `$in`/`$all` blowups.
- **`max_filters`** caps the number of simultaneous filters per request.
- **`max_limit` / `default_limit`** on pagination; an unbounded `limit` is never
  allowed.
- **Sortable-field allow-list** (default = filterable set) prevents sorting on
  unindexed fields by surprise.
- **Field allow-list in FilterSet** prevents accidental field exposure.

All guards are config knobs with safe defaults — the library is safe out of the
box and tunable when you know your indexes.

Continue to **[03 — Architecture](03-architecture.md)**.
