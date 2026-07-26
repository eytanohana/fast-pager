"""Shared test models for the fast-pager test suite."""

import enum
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

import pytest
from pydantic import BaseModel, Field

from fast_pager import Filterable, ops


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class User(BaseModel):
    """The workhorse model covering every Stage 1 scalar kind."""

    name: str
    age: int
    score: float
    balance: Decimal
    active: bool
    created_at: datetime
    birthday: date
    wakes_at: time
    uid: UUID
    color: Color
    status: Literal["active", "trial", "banned"]
    nickname: Optional[str] = None


class Aliased(BaseModel):
    user_name: str = Field(alias="userName")
    age: int


class Curated(BaseModel):
    """The Stage 2 workhorse: one field per `Filterable` knob."""

    name: Annotated[str, Filterable(ops=["contains", "eq"])]
    slug: Annotated[str, Filterable(ops=ops.ALL)]
    age: Annotated[int, Filterable(source="ageYears")]
    score: Annotated[float, Filterable(param="points")]
    ssn: Annotated[str, Filterable(ops=ops.NONE)]
    joined: Annotated[date, Filterable(ops=ops.NONE, sortable=True)]
    email: Annotated[str, Filterable(sortable=False)]


class Tagged(BaseModel):
    """The Phase 3a workhorse: arrays of scalars in every supported shape."""

    name: str
    tags: list[str]
    scores: list[int]
    colors: list[Color]
    codes: set[int]
    labels: Optional[list[str]] = None


@pytest.fixture(autouse=True)
def _clear_plan_cache():
    """Isolate the per-(model, config) plan cache between tests."""
    from fast_pager.params import _PLAN_CACHE

    _PLAN_CACHE.clear()
    yield
    _PLAN_CACHE.clear()
