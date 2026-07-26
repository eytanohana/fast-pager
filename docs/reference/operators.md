---
icon: lucide/table
---

# Operator Reference

!!! note "Scalar types only (for now)"
    The current release supports the **scalar types** below. Compound types —
    `list[T]`, nested Pydantic models, `dict[str, T]` and arrays of nested
    models — are designed (see
    [design doc 02](../design/02-type-and-operator-system.md)) but land in
    **`v0.2.0`** together with `FilterSet`. This page tracks the scalar
    tables only; it will grow compound-type tables once those ship.

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

List-value operators (`in`, `nin`) accept both comma-joined values and
repeated query keys; each element is coerced to the field's type. A
`max_list_length` guard (default 100) caps how many values are accepted.

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
