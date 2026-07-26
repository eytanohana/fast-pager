"""Tests for model introspection: each scalar type, Optional, aliases, skips."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

import pytest
from pydantic import BaseModel, Field

from conftest import Aliased, Color, Tagged, User
from fast_pager import ConfigurationError, Filterable, ops
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
        blobs: list[bytes]  # arrays are supported, but not of unsupported scalars
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


def test_filterable_param_renames_public_name_only():
    class M(BaseModel):
        age: Annotated[int, Filterable(param="minimum_age")]

    spec = spec_map(M)["minimum_age"]
    assert spec.public_name == "minimum_age"
    assert spec.source == "age"


def test_filterable_source_renames_backend_name_only():
    class M(BaseModel):
        age: Annotated[int, Filterable(source="ageYears")]

    spec = spec_map(M)["age"]
    assert spec.source == "ageYears"


def test_filterable_param_and_source_beat_the_alias_independently():
    class M(BaseModel):
        user_name: Annotated[str, Filterable(param="who")] = Field(alias="userName")
        age: Annotated[int, Filterable(source="ageYears")] = Field(alias="userAge")

    specs = spec_map(M)
    # param overrides the public name; the alias remains the source default.
    assert specs["who"].source == "userName"
    # source overrides the backend name; the alias remains the public default.
    assert specs["userAge"].source == "ageYears"


def test_filterable_on_optional_annotated_field():
    class M(BaseModel):
        nick: Annotated[Optional[str], Filterable(ops=["eq"])] = None

    spec = spec_map(M)["nick"]
    assert spec.py_type is str
    assert spec.nullable is True
    assert tuple(spec.filterable.ops) == ("eq",)


def test_fields_without_filterable_have_none():
    assert spec_map(User)["name"].filterable is None


def test_duplicate_filterable_annotations_raise():
    class M(BaseModel):
        x: Annotated[int, Filterable(), Filterable(sortable=False)]

    with pytest.raises(ConfigurationError, match=r"'x'.*2 Filterable"):
        introspect_model(M)


def test_filterable_on_unsupported_type_raises_naming_field_and_type():
    class M(BaseModel):
        blob: Annotated[bytes, Filterable(ops=ops.ALL)]

    with pytest.raises(ConfigurationError, match=r"'blob'.*bytes"):
        introspect_model(M)


# ---------------------------------------------------------------------------
# Phase 3a: arrays of scalars → Container.LIST.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "element"),
    [("tags", str), ("scores", int), ("colors", Color), ("codes", int)],
)
def test_list_and_set_of_scalars_resolve_to_list_container(field, element):
    spec = spec_map(Tagged)[field]
    assert spec.container is Container.LIST
    assert spec.py_type == element
    assert spec.nullable is False
    assert spec.source == field


def test_optional_list_is_nullable_with_element_type():
    spec = spec_map(Tagged)["labels"]
    assert spec.container is Container.LIST
    assert spec.py_type is str
    assert spec.nullable is True


def test_unsupported_list_shapes_are_skipped():
    class Inner(BaseModel):
        city: str

    class M(BaseModel):
        ok: list[str]
        bare: list
        of_nested: list[Inner]
        of_bytes: list[bytes]
        of_optional: list[str | None]
        frozen: frozenset[str]

    assert set(spec_map(M)) == {"ok"}


def test_filterable_on_list_of_nested_models_raises_naming_field_and_type():
    class Inner(BaseModel):
        city: str

    class M(BaseModel):
        addresses: Annotated[list[Inner], Filterable(ops=["has"])]

    with pytest.raises(ConfigurationError, match=r"'addresses'.*not a supported"):
        introspect_model(M)


def test_filterable_source_and_param_apply_to_list_fields():
    class M(BaseModel):
        tags: Annotated[list[str], Filterable(param="labels", source="tagList")]

    spec = spec_map(M)["labels"]
    assert spec.container is Container.LIST
    assert spec.source == "tagList"
