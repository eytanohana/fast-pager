---
icon: lucide/list-checks
---

# FilterSets: allow-list filter surfaces

Zero-config filtering exposes every supported field; `Filterable` and
`FilterConfig` let you curate that surface. A **`FilterSet`** flips the
model: instead of opting fields *out*, you declare a class that opts fields
*in* — and everything not listed simply is not filterable. This is the
"pro" tier of [design doc 01](../design/01-developer-experience.md) (Option
B), built for public APIs and for serving **multiple filter surfaces from
one model**.

```python
from fast_pager import FilterDepends, FilterQuery, FilterSet

class UserFilter(FilterSet):
    class Meta:
        model = User
        fields = {
            "name": ["contains", "startswith"],
            "age":  ["gte", "lte"],
            # fields omitted here are NOT filterable
        }

@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(UserFilter)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

`FilterDepends(UserFilter)` yields the exact same `FilterQuery` object as
`FilterDepends(User)` — `to_ast()`, `to_mongo()`, `sort_mongo()`, `skip`,
`limit`, `applied` — so graduating a route from zero-config to a FilterSet
never changes the call site.

## The `fields` mapping is a strict allow-list

Keys use the **public dotted-param spelling** — the same names clients type
in the URL: `"name"`, `"address__city"`, `"orders__elem__amount"`,
`"metadata__region"`, and the `Filterable(param=...)` name for a renamed
field. Values are exact operator lists, validated against the field's type
**at class definition** — a typo or a type mismatch fails at import with the
usual message naming the field, the operator, and the valid alternatives.

Two things make this the safe posture for public APIs (design doc 02):

- **If it's not listed, it's not filterable.** Adding a field to the model
  can never silently widen the filter surface.
- Listing a nested field (`"address__city"`) lists *exactly that path* —
  there is no subtree wildcard, and listing the embedding field alone does
  not enable its children.

The value `"__all__"` (or `ops.ALL`) means "every operator this field's
type supports" — still subject to the `allow_regex` gate, exactly like
`Filterable(ops=ops.ALL)`; listing `"regex"` explicitly is the eyes-open
opt-in. Listing an `elem` path is the explicit opt-in for element matching
(no `full` profile needed), and enumerated map keys stay `eq`-only.

## Where a FilterSet sits in the precedence ladder

The `fields` mapping occupies **layer 4** of the
[operator precedence ladder](controlling-fields.md#the-precedence-ladder) —
the same layer as `FilterConfig.operators`, which it *replaces* for
FilterSet routes. It beats field-level `Filterable(ops=...)`, per-type
`type_profiles`, and the global profile. The model-level absolutes remain
absolute:

- a `Filterable(ops=ops.NONE)` field cannot be listed — that's a
  `ConfigurationError`, not a quiet resurrection;
- a `Filterable(sortable=False)` field cannot be named in `Meta.sortable`.

`Filterable(source=...)` and `Filterable(param=...)` renames carry over
unchanged: the mapping is keyed by the public name, and compilation still
targets the source name.

## `Meta` in full

```python
class AdminUserFilter(FilterSet):
    class Meta:
        model = User                                  # required
        fields = {"name": "__all__", "age": ["gte", "lte"]}
        config = FilterConfig(unknown_params="strict", max_limit=500)
        sortable = ["name", "age", "last_login"]
```

- **`model`** (required) — the Pydantic model the surface derives from.
- **`fields`** — the allow-list mapping; defaults to `{}` (nothing but
  declared filters, pagination, and sort).
- **`config`** — a `FilterConfig` for everything that is *not* the
  allow-list: limits, `default_profile`, `allow_regex`, `type_profiles`,
  strict mode, `separator`, `max_depth`. Its `operators`, `exclude`, and
  `sortable` knobs are **rejected** here — the FilterSet spellings
  (`fields`, omission, `Meta.sortable`) replace them, and accepting both
  would create two competing sources of truth.
- **`sortable`** — optional sortable allow-list (it may name fields that
  are not filterable). Without it the default is *sortable iff listed and
  scalar*, plus `Filterable(sortable=True)` fields, minus
  `Filterable(sortable=False)` ones.

## Custom declared filters

Some parameters aren't derivable from a single generated `field__op` name.
Declare them as class attributes with `Filter`:

```python
from fast_pager import Filter

class AdminUserFilter(FilterSet):
    class Meta:
        model = User
        fields = {"name": ["contains"]}

    active_since = Filter(
        field="last_login",
        op="gte",
        description="Users whose last login is on or after this instant.",
    )
```

The **attribute name is the public parameter name** (`?active_since=...`;
override it with `param=`). The value type derives from the target field
and operator — `?active_since=banana` is a normal 422 — and the condition
compiles through the standard AST path:
`Condition(field="last_login", op="gte", value=datetime(...))`. The target
field does not have to appear in `fields` (a declared filter is its own
opt-in), but `ops.NONE` fields stay final. Declared filters are inherited:
a base class without a `Meta` can hold shared `Filter` declarations for
several concrete FilterSets.

## Public vs. admin: two surfaces, one model

Because the filter surface lives outside the model, several FilterSets over
the same model coexist trivially:

```python
class PublicUserFilter(FilterSet):
    class Meta:
        model = User
        fields = {"name": ["contains", "startswith"], "age": ["gte", "lte"]}

class AdminUserFilter(FilterSet):
    class Meta:
        model = User
        fields = {"name": "__all__", "age": "__all__", "email": ["eq"]}
        config = FilterConfig(unknown_params="strict")

    active_since = Filter(field="last_login", op="gte")

@app.get("/users")
async def public_users(q: FilterQuery[User] = FilterDepends(PublicUserFilter)): ...

@app.get("/admin/users")
async def admin_users(q: FilterQuery[User] = FilterDepends(AdminUserFilter)): ...
```

Each route documents exactly its own parameters in `/docs`, and an
`?email=` sent to the public route is unknown there — ignored by default, a
422 under the admin surface's strict mode.

A FilterSet carries its whole configuration in `Meta`, so
`FilterDepends(AdminUserFilter, config=...)` is ambiguous and raises
`ConfigurationError`; a FilterSet subclass without any `Meta` is *abstract*
(useful for shared `Filter` declarations) and cannot back a route.

A complete runnable app using all three declaration styles — zero-config,
`Filterable`, and a public/admin FilterSet pair — lives in
[`examples/mongo_app`](https://github.com/eytanohana/fast-pager/tree/main/examples/mongo_app).
