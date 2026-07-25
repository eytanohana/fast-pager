"""Shared test models for the fast-pager test suite."""

import enum
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

import pytest
from pydantic import BaseModel, Field


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


@pytest.fixture(autouse=True)
def _clear_plan_cache():
    """Isolate the per-(model, config) plan cache between tests."""
    from fast_pager.params import _PLAN_CACHE

    _PLAN_CACHE.clear()
    yield
    _PLAN_CACHE.clear()
