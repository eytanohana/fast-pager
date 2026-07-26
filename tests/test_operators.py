"""Tests for the operator registry and per-type profiles."""

import enum
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

import pytest

from fast_pager.operators import (
    DEFAULT_REGISTRY,
    Arity,
    Container,
    Tier,
    ValueTypeRule,
    all_operators_for,
    operators_for,
    type_kind,
)


class Color(enum.Enum):
    RED = "red"


class IntColor(enum.IntEnum):
    RED = 1


@pytest.mark.parametrize(
    ("py_type", "kind"),
    [
        (str, "str"),
        (int, "number"),
        (float, "number"),
        (Decimal, "number"),
        (bool, "bool"),
        (datetime, "temporal"),
        (date, "temporal"),
        (time, "temporal"),
        (UUID, "uuid"),
        (Color, "enum"),
        (IntColor, "enum"),  # Enum wins over its int base
        (Literal["a", "b"], "enum"),
        (bytes, None),
        (dict, None),
        (object, None),
        (Literal["a"] | None, None),  # unions are not scalar kinds
    ],
)
def test_type_kind(py_type, kind):
    assert type_kind(py_type) == kind


@pytest.mark.parametrize(
    ("py_type", "safe_ops"),
    [
        (str, {"eq", "ne", "in", "nin", "contains", "startswith", "endswith"}),
        (int, {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin"}),
        (Decimal, {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin"}),
        (bool, {"eq"}),
        (datetime, {"eq", "ne", "gt", "gte", "lt", "lte"}),
        (UUID, {"eq", "ne", "in", "nin"}),
        (Color, {"eq", "ne", "in", "nin"}),
        (Literal["a"], {"eq", "ne", "in", "nin"}),
    ],
)
def test_safe_profiles(py_type, safe_ops):
    assert set(operators_for(py_type, nullable=False, profile="safe")) == safe_ops


def test_full_profile_extends_safe():
    safe = set(operators_for(str, nullable=False, profile="safe"))
    full = set(operators_for(str, nullable=False, profile="full"))
    assert safe < full
    assert {"icontains", "istartswith", "iendswith", "regex", "text_search"} <= full


def test_full_profile_numbers_and_temporals_add_between():
    assert "between" in operators_for(int, nullable=False, profile="full")
    assert "between" in operators_for(date, nullable=False, profile="full")
    assert "ne" in operators_for(bool, nullable=False, profile="full")


def test_nullable_adds_isnull_and_exists():
    safe = operators_for(str, nullable=True, profile="safe")
    full = operators_for(str, nullable=True, profile="full")
    assert "isnull" in safe and "exists" not in safe
    assert "isnull" in full and "exists" in full


def test_unsupported_type_has_no_operators():
    assert operators_for(bytes, nullable=False, profile="full") == ()


def test_all_operators_for_is_full_tier():
    assert set(all_operators_for(int, nullable=True)) == set(
        operators_for(int, nullable=True, profile="full")
    )


def test_registry_records():
    assert DEFAULT_REGISTRY["regex"].tier is Tier.FULL
    assert DEFAULT_REGISTRY["eq"].tier is Tier.SAFE
    assert DEFAULT_REGISTRY["in"].arity is Arity.LIST
    assert DEFAULT_REGISTRY["between"].arity is Arity.RANGE
    assert DEFAULT_REGISTRY["isnull"].arity is Arity.BOOL


# ---------------------------------------------------------------------------
# Phase 3a: the array (Container.LIST) operator profile.
# ---------------------------------------------------------------------------

ARRAY_SAFE = {"has", "has_any", "has_all", "len__eq", "empty"}
ARRAY_FULL = {"len__ne", "len__gt", "len__gte", "len__lt", "len__lte"}


@pytest.mark.parametrize("element", [str, int, Decimal, datetime, UUID, Color, Literal["a"]])
def test_array_safe_profile_is_element_type_agnostic(element):
    ops = operators_for(element, nullable=False, profile="safe", container=Container.LIST)
    assert set(ops) == ARRAY_SAFE


def test_array_full_profile_adds_the_len_comparisons():
    ops = operators_for(str, nullable=False, profile="full", container=Container.LIST)
    assert set(ops) == ARRAY_SAFE | ARRAY_FULL


def test_nullable_array_adds_isnull_and_exists():
    safe = operators_for(str, nullable=True, profile="safe", container=Container.LIST)
    full = operators_for(str, nullable=True, profile="full", container=Container.LIST)
    assert "isnull" in safe and "exists" not in safe
    assert "isnull" in full and "exists" in full


def test_array_of_unsupported_element_has_no_operators():
    assert operators_for(bytes, nullable=False, profile="full", container=Container.LIST) == ()


def test_all_operators_for_accepts_the_container():
    assert set(all_operators_for(str, nullable=False, container=Container.LIST)) == (
        ARRAY_SAFE | ARRAY_FULL
    )


# ---------------------------------------------------------------------------
# Phase 3b: the embedding (Container.NESTED) operator surface.
# ---------------------------------------------------------------------------


def test_nested_container_exposes_only_nullability_operators():
    assert operators_for(object, nullable=False, profile="full", container=Container.NESTED) == ()
    safe = operators_for(object, nullable=True, profile="safe", container=Container.NESTED)
    full = operators_for(object, nullable=True, profile="full", container=Container.NESTED)
    assert set(safe) == {"isnull"}
    assert set(full) == {"isnull", "exists"}


def test_nullability_operators_apply_to_nested_container():
    assert Container.NESTED in DEFAULT_REGISTRY["isnull"].applies_to
    assert Container.NESTED in DEFAULT_REGISTRY["exists"].applies_to
    assert Container.NESTED not in DEFAULT_REGISTRY["eq"].applies_to


def test_array_registry_records():
    assert DEFAULT_REGISTRY["has"].arity is Arity.SINGLE
    assert DEFAULT_REGISTRY["has"].value_type is ValueTypeRule.SAME_AS_FIELD
    assert DEFAULT_REGISTRY["has_any"].arity is Arity.LIST
    assert DEFAULT_REGISTRY["has_all"].arity is Arity.LIST
    assert DEFAULT_REGISTRY["len__eq"].value_type is ValueTypeRule.INT
    assert DEFAULT_REGISTRY["len__eq"].tier is Tier.SAFE
    assert DEFAULT_REGISTRY["len__gte"].tier is Tier.FULL  # $expr — no index support
    assert DEFAULT_REGISTRY["empty"].arity is Arity.BOOL
    assert DEFAULT_REGISTRY["has"].applies_to == frozenset({Container.LIST})
    assert Container.LIST in DEFAULT_REGISTRY["isnull"].applies_to
