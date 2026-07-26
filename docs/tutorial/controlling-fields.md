---
icon: lucide/sliders-horizontal
---

# Controlling the filter surface

Zero-config `FilterDepends(Model)` exposes every supported field with its
type's `safe` operator profile. That's the right starting point — but real
APIs need to curate: exact-match-only emails, unfilterable secrets, a Mongo
field named differently from the model, a friendlier URL parameter. This
page covers the two tools for that: **`Filterable`** metadata on the model
and **`FilterConfig`** on the route.

## `Filterable`: declare it on the field

Attach metadata where the field is declared, with `Annotated`:

```python
from typing import Annotated
from pydantic import BaseModel
from fast_pager import Filterable, ops

class User(BaseModel):
    name: Annotated[str, Filterable(ops=["contains", "eq"])]
    slug: Annotated[str, Filterable(ops=ops.ALL)]
    age: Annotated[int, Filterable(source="ageYears")]
    score: Annotated[float, Filterable(param="points")]
    ssn: Annotated[str, Filterable(ops=ops.NONE)]
    email: Annotated[str, Filterable(sortable=False)]
```

Every knob defaults to "no opinion" — a bare `Filterable()` changes nothing,
and fields without one behave exactly as before.

### `ops=[...]` — an exact operator allow-list

`name` above generates only `name` / `name__eq` / `name__contains` — nothing
else. Operator names are validated against the field's type when the route
is registered, so a typo or a type mismatch fails at startup with a message
naming the field, the operator, and what would be valid:

```text
ConfigurationError: operator 'contains' is not valid for field 'age' of
type int in Filterable(ops=...); valid operators for int: eq, ne, gt, gte,
lt, lte, in, nin, between
```

Two marker spellings cover the extremes:

- **`ops=ops.ALL`** — everything the type supports (the `full` tier), still
  subject to the `allow_regex` gate. `slug` above gets `icontains`,
  `text_search`, etc., but `slug__regex` only appears with
  `FilterConfig(allow_regex=True)`.
- **`ops=ops.NONE`** — explicitly unfilterable: the field generates no
  parameters at all. This is final — naming the field in
  `FilterConfig.operators` is a `ConfigurationError`, so a route can never
  quietly resurrect a field the model opted out.

Listing `"regex"` explicitly (`Filterable(ops=["regex"])`) *does* bypass the
gate — an explicit per-field list is the eyes-open opt-in.

### `source=` — a different backend field name

When the Mongo document key differs from the model field, `source` points
the compiled query at it while the URL keeps the model name:

```text
GET /users?age__gte=21
```

```python
q.to_mongo()   # -> {"ageYears": {"$gte": 21}}
q.sort_mongo() # ?sort=-age -> [("ageYears", -1)]
```

### `param=` — a different URL parameter name

The mirror image: `param` renames the *public* side and leaves the backend
alone.

```text
GET /users?points__gte=1.5
```

```python
q.to_mongo()   # -> {"score": {"$gte": 1.5}}
```

`FilterConfig` entries (`operators=`, `exclude=`, `sortable=`) always refer
to the **public** name — `"points"`, not `"score"`.

!!! info "How `param`, `source`, and Pydantic aliases interact"
    Each field has two names, resolved independently:

    - **public** (query parameter): `param` if set, else the Pydantic
      alias, else the field name;
    - **source** (compiled query): `source` if set, else the Pydantic
      alias, else the field name.

    An alias alone therefore still renames both at once (the Stage 1
    behavior); `param`/`source` pull the two apart when you need them to
    differ.

### `sortable=` — per-field sort control

- `Filterable(sortable=False)` removes the field from the sortable set even
  though it stays filterable. Like `ops.NONE`, it is final: listing the
  field in `FilterConfig.sortable` raises a `ConfigurationError`.
- `Filterable(sortable=True)` adds the field even when it isn't filterable —
  combine it with `ops.NONE` for a **sort-only** field:

```python
joined: Annotated[date, Filterable(ops=ops.NONE, sortable=True)]
```

```text
GET /users?sort=-joined     # fine
GET /users?joined__gte=...  # no such parameter
```

## `FilterConfig(type_profiles=...)`: per-type overrides

"All strings in this app expose `icontains` but never `regex`" is one line
on the route (or in a shared config object):

```python
FilterConfig(type_profiles={str: ["eq", "contains", "icontains"]})
```

Profiles are keyed by the resolved (Optional-unwrapped) field type and are
validated when the config is constructed — an unknown operator or one
invalid for the keyed type raises `ConfigurationError` immediately.
Subclasses match through the MRO with the most specific key winning, so
`{enum.Enum: [...]}` covers every enum and `{bool: [...]}` beats
`{int: [...]}` for `bool` fields. Nullable-only operators (`isnull`,
`exists`) may appear in a profile; they are simply not emitted for
non-nullable fields.

## The precedence ladder

Four layers, finest wins (design doc 02):

| Layer | Where | Scope |
|---|---|---|
| 4. `FilterConfig(operators={"field": [...]})` | route | one field, one endpoint |
| 3. `Filterable(ops=[...])` | model | one field, everywhere |
| 2. `FilterConfig(type_profiles={T: [...]})` | route | every field of type `T` |
| 1. `FilterConfig(default_profile=...)` | route | everything |

Two absolutes sit outside the ladder: `Filterable(ops=ops.NONE)` and
`Filterable(sortable=False)` cannot be overridden by any config — model-level
opt-outs are a security posture, not a default.

```python
class User(BaseModel):
    name: Annotated[str, Filterable(ops=["contains", "eq"])]  # layer 3
    bio: str                                                  # layers 1/2 apply

config = FilterConfig(
    type_profiles={str: ["eq", "icontains"]},   # layer 2
    operators={"name": ["startswith"]},         # layer 4
)
# name  -> startswith only            (4 beats 3)
# bio   -> eq, icontains              (2 beats 1)
```

## Strict unknown-parameter mode

By default, unrecognized query parameters are ignored — forgiving, and safe
because unmatched parameters are never guessed at. During development,
though, a typo like `?nmae__eq=alice` silently returning the full collection
hides bugs. Strict mode turns it into a standard 422:

```python
FilterDepends(User, config=FilterConfig(unknown_params="strict"))
```

```text
GET /users?nmae__eq=alice
-> 422 {"detail": [{"type": "unrecognized_filter",
                    "loc": ["query", "nmae__eq"],
                    "msg": "Unrecognized filter parameter 'nmae__eq'", ...}]}
```

**The exact rule:** a parameter is rejected when it is *not* one of the
generated (or reserved `limit`/`offset`/`sort`) names **and** it contains
the configured separator (`__` by default) — i.e. it claims to be a
`field__op` filter. This includes a real field with an unavailable operator
(`name__regex` while regex is gated). Parameters *without* the separator are
never rejected, because your route may legitimately declare its own
(`?verbose=true`) and the dependency cannot see them.

!!! warning "Route parameters containing the separator"
    The corollary: with strict mode on, don't name your route's own query
    parameters with the separator in them (`my__flag`) — strict mode would
    reject requests that use them. Rename the parameter or stay on
    `"ignore"` for that route.

## Next

The [Operator Reference](../reference/operators.md) lists every operator
each type supports — the vocabulary `ops=[...]` and `type_profiles` draw
from. For the design rationale behind the layering, see
[design doc 02](../design/02-type-and-operator-system.md#per-field-configurability-the-core-question-you-asked).
