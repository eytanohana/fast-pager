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
