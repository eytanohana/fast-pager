"""Tests for model introspection: each scalar type, Optional, aliases, skips."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Optional, Union
from uuid import UUID

import pytest
from pydantic import BaseModel

from conftest import Aliased, Color, User
from fast_pager.introspection import introspect_model, public_field_names
from fast_pager.operators import Container


def spec_map(model):
    return {s.public_name: s for s in introspect_model(model)}


@pytest.mark.parametrize(
    ("field", "py_type"),
    [
        ("name", str),
        ("age", int),
        ("score", float),
        ("balance", Decimal),
        ("active", bool),
        ("created_at", datetime),
        ("birthday", date),
        ("wakes_at", time),
        ("uid", UUID),
        ("color", Color),
        ("status", Literal["active", "trial", "banned"]),
    ],
)
def test_each_scalar_type(field, py_type):
    spec = spec_map(User)[field]
    assert spec.py_type == py_type
    assert spec.container is Container.SCALAR
    assert spec.nullable is False
    assert spec.source == field
    assert spec.path == (field,)


def test_optional_is_unwrapped_and_nullable():
    spec = spec_map(User)["nickname"]
    assert spec.py_type is str
    assert spec.nullable is True


def test_pipe_union_optional():
    class M(BaseModel):
        x: int | None = None

    spec = spec_map(M)["x"]
    assert spec.py_type is int and spec.nullable


def test_alias_is_public_and_source_name():
    specs = spec_map(Aliased)
    assert "userName" in specs and "user_name" not in specs
    assert specs["userName"].source == "userName"


def test_unsupported_fields_are_skipped():
    class M(BaseModel):
        ok: str
        blob: bytes
        tags: list[str]
        meta: dict[str, str]
        either: Union[int, str]
        maybe_either: Optional[Union[int, str]] = None

    assert set(spec_map(M)) == {"ok"}


def test_nested_models_are_skipped_in_stage_1():
    class Inner(BaseModel):
        city: str

    class Outer(BaseModel):
        inner: Inner
        name: str

    assert set(spec_map(Outer)) == {"name"}


def test_public_field_names_includes_unfilterable_fields():
    class M(BaseModel):
        ok: str
        blob: bytes

    assert public_field_names(M) == {"ok", "blob"}
