"""Tests for model introspection: each scalar type, Optional, aliases, skips."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

import pytest
from pydantic import BaseModel, Field

from conftest import Address, Aliased, Color, Customer, Tagged, User
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


# ---------------------------------------------------------------------------
# Phase 3b: nested Pydantic models → multi-segment paths, dotted sources.
# ---------------------------------------------------------------------------


def test_nested_scalar_fields_get_multi_segment_paths_and_dotted_sources():
    city = spec_map(Customer)["address__city"]
    assert city.path == ("address", "city")
    assert city.source == "address.city"
    assert city.py_type is str
    assert city.container is Container.SCALAR
    assert city.nullable is False


def test_embedding_field_gets_its_own_nested_spec():
    addr = spec_map(Customer)["address"]
    assert addr.container is Container.NESTED
    assert addr.py_type is Address
    assert addr.nullable is False
    assert addr.source == "address"


def test_optional_nested_model_is_nullable_and_children_still_generated():
    specs = spec_map(Customer)
    assert specs["billing"].nullable is True
    assert specs["billing"].container is Container.NESTED
    assert specs["billing__city"].container is Container.SCALAR
    assert specs["billing__city"].source == "billing.city"


def test_arrays_inside_nested_models_resolve_to_list_container():
    spec = spec_map(Customer)["address__tags"]
    assert spec.container is Container.LIST
    assert spec.py_type is str
    assert spec.source == "address.tags"


def test_nested_source_override_composes_into_the_dotted_path():
    assert spec_map(Customer)["address__zip_code"].source == "address.zip"


def test_source_on_the_embedding_field_renames_that_segment():
    class M(BaseModel):
        address: Annotated[Address, Filterable(source="addr")]

    specs = spec_map(M)
    assert specs["address"].source == "addr"
    assert specs["address__zip_code"].source == "addr.zip"
    assert specs["address__geo__lat"].source == "addr.geo.lat"


def test_param_on_nested_and_embedding_fields_renames_public_segments():
    class Inner(BaseModel):
        postal_code: Annotated[str, Filterable(param="zip")]

    class M(BaseModel):
        shipping_address: Annotated[Inner, Filterable(param="addr")]

    specs = spec_map(M)
    assert specs["addr__zip"].source == "shipping_address.postal_code"


def test_alias_defaults_both_names_in_nested_paths():
    class Inner(BaseModel):
        postal_code: str = Field(alias="postalCode")

    class M(BaseModel):
        address: Inner

    assert spec_map(M)["address__postalCode"].source == "address.postalCode"


def test_fields_beyond_the_depth_bound_are_silently_skipped():
    class L3(BaseModel):
        x: int

    class L2(BaseModel):
        leaf: int
        l3: L3

    class L1(BaseModel):
        l2: L2

    class Root(BaseModel):
        l1: L1

    specs = spec_map(Root)
    assert "l1__l2__leaf" in specs  # exactly 2 model boundaries below the root
    # An embedding sitting exactly at the bound keeps its own spec...
    assert "l1__l2__l3" in specs
    # ...but its children are 3 boundaries deep and are skipped.
    assert "l1__l2__l3__x" not in specs


def test_max_depth_is_configurable():
    one = {s.public_name for s in introspect_model(Customer, max_depth=1)}
    assert "address__city" in one
    assert "address__geo" in one  # the embedding at the bound keeps its spec
    assert "address__geo__lat" not in one
    zero = {s.public_name for s in introspect_model(Customer, max_depth=0)}
    assert "address" in zero and "billing" in zero
    assert "address__city" not in zero


def test_self_referential_model_truncates_at_the_depth_bound():
    class Node(BaseModel):
        value: int
        parent: Optional["Node"] = None

    Node.model_rebuild()
    specs = spec_map(Node)
    assert {"value", "parent", "parent__value", "parent__parent"} <= set(specs)
    assert "parent__parent__value" in specs  # 2 boundaries: still in
    assert "parent__parent__parent__value" not in specs  # 3 boundaries: out


def test_mutually_recursive_models_truncate_at_the_depth_bound():
    class A(BaseModel):
        name: str
        b: Optional["B"] = None

    class B(BaseModel):
        title: str
        a: Optional[A] = None

    A.model_rebuild(_types_namespace={"B": B})
    specs = spec_map(A)
    assert {"b__title", "b__a__name"} <= set(specs)
    assert "b__a__b__title" not in specs


def test_ops_none_on_the_embedding_field_excludes_the_whole_subtree():
    class M(BaseModel):
        name: str
        address: Annotated[Address, Filterable(ops=ops.NONE)]

    specs = spec_map(M)
    assert "address" in specs  # the spec exists and carries the opt-out
    assert not any(name.startswith("address__") for name in specs)


def test_custom_separator_joins_nested_public_names():
    specs = {s.public_name for s in introspect_model(Customer, separator=".")}
    assert "address.geo.lat" in specs


def test_list_of_nested_models_and_dicts_stay_skipped():
    class M(BaseModel):
        ok: str
        addresses: list[Address]
        meta: dict[str, str]

    assert set(spec_map(M)) == {"ok"}
