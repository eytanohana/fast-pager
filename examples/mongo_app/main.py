"""A runnable fast-pager demo app over a non-trivial model set.

One ``User`` model — with a nested ``Address``, a ``list[str]`` of tags, a
``list[Order]``, and a ``dict[str, str]`` metadata map — exposed through all
three declaration styles (design doc 01):

- ``GET /users`` — zero-config ``FilterDepends(User)``, shaped by the
  ``Filterable`` metadata on the model (Options C + A);
- ``GET /public/users`` — a small, strict allow-list ``FilterSet``;
- ``GET /admin/users`` — a wide ``FilterSet`` over the *same* model, with a
  custom declared filter (``?active_since=``).

The app needs **no MongoDB**: every endpoint returns the query it *would*
run — the compiled filter dict, sort list, and pagination window — which is
also exactly what the test suite asserts against. With a real database the
handler body would be, e.g.::

    await db.users.find(q.to_mongo()).sort(q.sort_mongo() or None) \\
        .skip(q.skip).limit(q.limit).to_list(None)

Run it from this directory with ``uvicorn main:app --reload`` and explore
the generated parameters at http://127.0.0.1:8000/docs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from fast_pager import (
    Filter,
    FilterConfig,
    FilterDepends,
    FilterQuery,
    FilterSet,
    Filterable,
    ops,
)

# ---------------------------------------------------------------------------
# The models (the development plan's example set: users + addresses + tags +
# orders + an enumerated-key metadata map).
# ---------------------------------------------------------------------------


class Address(BaseModel):
    city: str
    country: str
    # The Mongo documents store this as `zip`; the model (and URL) say
    # `zip_code`.
    zip_code: Annotated[str, Filterable(source="zip")]


class Order(BaseModel):
    amount: float
    status: Literal["pending", "paid", "refunded"]
    placed_at: datetime


class User(BaseModel):
    name: str
    # Option A metadata, applied wherever the model is used zero-config:
    # exact-match-only email, never-filterable password hash.
    email: Annotated[str, Filterable(ops=["eq"])]
    password_hash: Annotated[str, Filterable(ops=ops.NONE)]
    age: int
    active: bool
    last_login: Optional[datetime] = None
    address: Address
    tags: list[str]
    orders: list[Order]
    # Maps are unfilterable unless enabled; these two keys get typed params.
    metadata: Annotated[dict[str, str], Filterable(keys=["region", "tier"])]


# ---------------------------------------------------------------------------
# Two FilterSets over the same model: a public surface and an admin surface.
# ---------------------------------------------------------------------------


class PublicUserFilter(FilterSet):
    """The public API surface: a deliberate, minimal allow-list."""

    class Meta:
        model = User
        fields = {
            "name": ["contains", "startswith"],
            "age": ["gte", "lte"],
            "tags": ["has"],
            "address__city": ["eq"],
        }


class AdminUserFilter(FilterSet):
    """The admin surface: wide, strict about typos, custom `active_since`."""

    class Meta:
        model = User
        fields = {
            "name": "__all__",
            "email": ["eq"],
            "age": "__all__",
            "active": ["eq"],
            "tags": ["has", "has_any", "len__eq", "empty"],
            "address__city": ["eq", "contains"],
            "address__country": ["eq"],
            "address__zip_code": ["eq"],
            "orders": ["len__eq", "empty"],
            # Listing `elem` paths is the explicit opt-in for same-element
            # matching (one $elemMatch per request across these params).
            "orders__elem__amount": ["gte", "lte"],
            "orders__elem__status": ["eq", "in"],
            "metadata": ["has_key"],
            "metadata__region": ["eq"],
            "metadata__tier": ["eq"],
        }
        config = FilterConfig(unknown_params="strict", max_limit=500)
        sortable = ["name", "age", "last_login"]

    active_since = Filter(
        field="last_login",
        op="gte",
        description="Users whose last login is on or after this instant.",
    )


# ---------------------------------------------------------------------------
# The app. Handlers return the compiled query instead of running it.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="fast-pager example app",
    description="Every endpoint returns the MongoDB query it would run.",
)


def compiled(q: FilterQuery[User]) -> dict[str, Any]:
    """The query a handler would hand to Mongo, as a JSON-friendly dict."""
    return {
        "filter": q.to_mongo(),
        "sort": q.sort_mongo(),
        "skip": q.skip,
        "limit": q.limit,
    }


@app.get("/users")
def list_users(q: FilterQuery[User] = FilterDepends(User)) -> dict[str, Any]:
    """Zero-config: every supported field, shaped by `Filterable` metadata."""
    return compiled(q)


@app.get("/public/users")
def list_users_public(
    q: FilterQuery[User] = FilterDepends(PublicUserFilter),
) -> dict[str, Any]:
    """The public allow-list surface: unlisted fields are not filterable."""
    return compiled(q)


@app.get("/admin/users")
def list_users_admin(
    q: FilterQuery[User] = FilterDepends(AdminUserFilter),
) -> dict[str, Any]:
    """The admin surface: same model, wider allow-list, strict mode."""
    return compiled(q)
