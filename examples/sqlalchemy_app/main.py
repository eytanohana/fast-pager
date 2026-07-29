"""The example app's endpoints on **SQLAlchemy** — only the backend swaps.

Same shape as ``examples/mongo_app``: a ``User`` Pydantic model (with a
nested ``Address``) declared once, exposed zero-config and through a strict
public ``FilterSet``. The endpoint signatures and the ``q`` object are
identical to the Mongo app's — the handler body swaps ``q.to_mongo()`` for
``q.apply_sqlalchemy(select(...))`` and actually **executes** the query
against an in-memory SQLite database, returning real rows.

The SQLAlchemy side of the pair is ``UserRow``: flat columns for the scalar
fields and a JSON column for the nested ``address`` (dotted filter paths
like ``?address__city=Amsterdam`` compile to JSON path access). Fields whose
operators SQL cannot express (array ``tags``, ``list[Order]`` element
matching, map ``has_key``) are simply not part of this model — and the
``backend=`` hook on ``FilterDepends`` guarantees at startup that every
generated parameter is compilable on this backend.

Run it from this directory with ``uvicorn main:app --reload`` and explore
the generated parameters at http://127.0.0.1:8000/docs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import JSON, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from fast_pager import FilterDepends, FilterQuery, FilterSet, Filterable, ops
from fast_pager.backends.sqlalchemy import SQLAlchemyCompiler

# ---------------------------------------------------------------------------
# The Pydantic model — the same declaration style as the Mongo app.
# ---------------------------------------------------------------------------


class Address(BaseModel):
    city: str
    country: str
    # Stored under the JSON key `zip`; the model (and URL) say `zip_code`.
    zip_code: Annotated[str, Filterable(source="zip")]


class User(BaseModel):
    name: str
    email: Annotated[str, Filterable(ops=["eq"])]
    password_hash: Annotated[str, Filterable(ops=ops.NONE)]
    age: int
    active: bool
    last_login: Optional[datetime] = None
    address: Address


class PublicUserFilter(FilterSet):
    """The public API surface: a deliberate, minimal allow-list."""

    class Meta:
        model = User
        fields = {
            "name": ["contains", "startswith"],
            "age": ["gte", "lte"],
            "address__city": ["eq"],
        }


# ---------------------------------------------------------------------------
# The SQLAlchemy model paired with it, plus a seeded in-memory database.
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]
    password_hash: Mapped[str]
    age: Mapped[int]
    active: Mapped[bool]
    last_login: Mapped[Optional[datetime]]
    address: Mapped[dict[str, Any]] = mapped_column(JSON)


# StaticPool + check_same_thread=False keep the single in-memory database
# shared across the threadpool FastAPI runs sync handlers on.
engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)

with Session(engine) as _session:
    _session.add_all(
        [
            UserRow(
                name="Ana",
                email="ana@example.com",
                password_hash="x",
                age=34,
                active=True,
                last_login=datetime(2026, 3, 1, 9, 0, 0),
                address={"city": "Amsterdam", "country": "NL", "zip": "1012"},
            ),
            UserRow(
                name="Anatoly",
                email="anatoly@example.com",
                password_hash="x",
                age=51,
                active=False,
                last_login=None,
                address={"city": "Berlin", "country": "DE", "zip": "10115"},
            ),
            UserRow(
                name="Bram",
                email="bram@example.com",
                password_hash="x",
                age=19,
                active=True,
                last_login=datetime(2026, 6, 15, 18, 30, 0),
                address={"city": "Amsterdam", "country": "NL", "zip": "1017"},
            ),
        ]
    )
    _session.commit()

#: The backend this app runs on. Passing it to FilterDepends() validates at
#: startup that every generated parameter is compilable on SQLAlchemy.
BACKEND = SQLAlchemyCompiler(UserRow)

# ---------------------------------------------------------------------------
# The app. Handlers execute the query and return real rows.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="fast-pager example app (SQLAlchemy)",
    description="The same endpoints as the Mongo example, executing on SQLite.",
)


def run_query(q: FilterQuery[User]) -> dict[str, Any]:
    """Execute the query against the database and page the results."""
    with Session(engine) as session:
        statement = q.apply_sqlalchemy(select(UserRow))
        rows = session.execute(statement).scalars().all()
        count = select(func.count()).select_from(UserRow)
        where = q.to_sqlalchemy(UserRow)
        if where is not None:
            count = count.where(where)
        total = session.execute(count).scalar_one()
    return {
        "items": [
            {
                "name": row.name,
                "email": row.email,
                "age": row.age,
                "active": row.active,
                "last_login": row.last_login,
                "address": row.address,
            }
            for row in rows
        ],
        "total": total,
        "limit": q.limit,
        "offset": q.offset,
    }


@app.get("/users")
def list_users(q: FilterQuery[User] = FilterDepends(User, backend=BACKEND)) -> dict[str, Any]:
    """Zero-config: every supported field, shaped by `Filterable` metadata."""
    return run_query(q)


@app.get("/public/users")
def list_users_public(
    q: FilterQuery[User] = FilterDepends(PublicUserFilter, backend=BACKEND),
) -> dict[str, Any]:
    """The public allow-list surface: unlisted fields are not filterable."""
    return run_query(q)
