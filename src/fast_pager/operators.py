"""Operator definitions, tiers, and the default per-type operator profiles.

Two registries drive everything (design doc 02):

1. type → operator profile (``safe`` / ``full`` tiers per scalar kind), and
2. operator → semantics (arity, value-type rule, applicable containers, tier).
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, get_origin
from uuid import UUID

__all__ = [
    "DEFAULT_REGISTRY",
    "Arity",
    "Container",
    "Operator",
    "Tier",
    "TypeProfile",
    "ValueTypeRule",
    "all_operators_for",
    "operators_for",
    "type_kind",
    "type_name",
]


class Container(enum.Enum):
    """The container shape of a model field (``SCALAR`` and ``LIST`` so far)."""

    SCALAR = "scalar"
    LIST = "list"
    NESTED = "nested"
    LIST_OF_NESTED = "list_of_nested"
    MAP = "map"


class Arity(enum.Enum):
    """How many values an operator takes and how they arrive."""

    SINGLE = "single"
    LIST = "list"
    RANGE = "range"
    BOOL = "bool"


class ValueTypeRule(enum.Enum):
    """The relationship between an operator's value type and the field type."""

    SAME_AS_FIELD = "same_as_field"
    BOOL = "bool"
    INT = "int"


class Tier(enum.Enum):
    """Safety tier: ``SAFE`` operators are exposed by default, ``FULL`` are opt-in."""

    SAFE = "safe"
    FULL = "full"


@dataclass(frozen=True)
class Operator:
    """A single operator record in the registry."""

    name: str
    arity: Arity
    value_type: ValueTypeRule
    applies_to: frozenset[Container]
    tier: Tier


_SCALAR = frozenset({Container.SCALAR})
_LIST = frozenset({Container.LIST})
_SCALAR_OR_LIST = frozenset({Container.SCALAR, Container.LIST})


def _op(
    name: str,
    arity: Arity = Arity.SINGLE,
    value_type: ValueTypeRule = ValueTypeRule.SAME_AS_FIELD,
    tier: Tier = Tier.SAFE,
    applies_to: frozenset[Container] = _SCALAR,
) -> Operator:
    return Operator(name=name, arity=arity, value_type=value_type, applies_to=applies_to, tier=tier)


DEFAULT_REGISTRY: dict[str, Operator] = {
    op.name: op
    for op in (
        _op("eq"),
        _op("ne"),
        _op("gt"),
        _op("gte"),
        _op("lt"),
        _op("lte"),
        _op("in", arity=Arity.LIST),
        _op("nin", arity=Arity.LIST),
        _op("between", arity=Arity.RANGE, tier=Tier.FULL),
        _op("contains"),
        _op("icontains", tier=Tier.FULL),
        _op("startswith"),
        _op("istartswith", tier=Tier.FULL),
        _op("endswith"),
        _op("iendswith", tier=Tier.FULL),
        # `regex` is FULL-tier *and* additionally gated by FilterConfig.allow_regex.
        _op("regex", tier=Tier.FULL),
        _op("text_search", tier=Tier.FULL),
        # Nullability operators apply to scalar *and* array fields alike.
        _op("isnull", arity=Arity.BOOL, value_type=ValueTypeRule.BOOL, applies_to=_SCALAR_OR_LIST),
        _op(
            "exists",
            arity=Arity.BOOL,
            value_type=ValueTypeRule.BOOL,
            tier=Tier.FULL,
            applies_to=_SCALAR_OR_LIST,
        ),
        # Array (list[T]/set[T]) operators: membership and shape, never the
        # element's scalar operators (design doc 02, arrays of scalars).
        _op("has", applies_to=_LIST),
        _op("has_any", arity=Arity.LIST, applies_to=_LIST),
        _op("has_all", arity=Arity.LIST, applies_to=_LIST),
        _op("len__eq", value_type=ValueTypeRule.INT, applies_to=_LIST),
        # The length comparisons compile to `$expr` (no index support), so
        # they are FULL-tier; `len__eq` compiles to plain `$size` and is SAFE.
        _op("len__ne", value_type=ValueTypeRule.INT, tier=Tier.FULL, applies_to=_LIST),
        _op("len__gt", value_type=ValueTypeRule.INT, tier=Tier.FULL, applies_to=_LIST),
        _op("len__gte", value_type=ValueTypeRule.INT, tier=Tier.FULL, applies_to=_LIST),
        _op("len__lt", value_type=ValueTypeRule.INT, tier=Tier.FULL, applies_to=_LIST),
        _op("len__lte", value_type=ValueTypeRule.INT, tier=Tier.FULL, applies_to=_LIST),
        _op("empty", arity=Arity.BOOL, value_type=ValueTypeRule.BOOL, applies_to=_LIST),
    )
}
"""The default operator registry: name → :class:`Operator`."""


@dataclass(frozen=True)
class TypeProfile:
    """Default operator names per tier for one scalar kind."""

    safe: tuple[str, ...]
    full: tuple[str, ...]


_PROFILES: dict[str, TypeProfile] = {
    "str": TypeProfile(
        safe=("eq", "ne", "in", "nin", "contains", "startswith", "endswith"),
        full=("icontains", "istartswith", "iendswith", "regex", "text_search"),
    ),
    "number": TypeProfile(
        safe=("eq", "ne", "gt", "gte", "lt", "lte", "in", "nin"),
        full=("between",),
    ),
    "bool": TypeProfile(safe=("eq",), full=("ne",)),
    "temporal": TypeProfile(
        safe=("eq", "ne", "gt", "gte", "lt", "lte"),
        full=("between",),
    ),
    "uuid": TypeProfile(safe=("eq", "ne", "in", "nin"), full=()),
    "enum": TypeProfile(safe=("eq", "ne", "in", "nin"), full=()),
}

_ARRAY_PROFILE = TypeProfile(
    safe=("has", "has_any", "has_all", "len__eq", "empty"),
    full=("len__ne", "len__gt", "len__gte", "len__lt", "len__lte"),
)
"""The single operator profile for ``list[T]``/``set[T]`` fields, whatever
the element type — array operators are about membership and shape, not the
element's scalar semantics (design doc 02)."""

_NULLABLE_SAFE: tuple[str, ...] = ("isnull",)
_NULLABLE_FULL: tuple[str, ...] = ("exists",)


def type_kind(py_type: Any) -> str | None:
    """Classify a resolved (Optional-unwrapped) type into a profile kind.

    Returns one of ``"str"``, ``"number"``, ``"bool"``, ``"temporal"``,
    ``"uuid"``, ``"enum"``, or ``None`` when the type is not a filterable
    scalar in this stage.
    """
    if get_origin(py_type) is Literal:
        return "enum"
    if not isinstance(py_type, type):
        return None
    # Order matters: bool is a subclass of int, Enum members may subclass int,
    # and datetime is a subclass of date.
    if issubclass(py_type, enum.Enum):
        return "enum"
    if issubclass(py_type, bool):
        return "bool"
    if issubclass(py_type, (int, float, Decimal)):
        return "number"
    if issubclass(py_type, str):
        return "str"
    if issubclass(py_type, (datetime.datetime, datetime.date, datetime.time)):
        return "temporal"
    if issubclass(py_type, UUID):
        return "uuid"
    return None


def type_name(py_type: Any) -> str:
    """Human-readable name of a resolved field type, for error messages.

    ``int`` renders as ``int`` (not ``<class 'int'>``); parameterized and
    typing forms (``list[bytes]``, ``Literal[...]``) keep their ``str()``
    representation, which spells out the parameters.
    """
    if get_origin(py_type) is not None:
        return str(py_type)
    name = getattr(py_type, "__name__", None)
    return name if isinstance(name, str) else str(py_type)


def operators_for(
    py_type: Any,
    nullable: bool,
    profile: Literal["safe", "full"],
    container: Container = Container.SCALAR,
) -> tuple[str, ...]:
    """Operator names a field exposes under the given profile tier.

    ``py_type`` is the resolved scalar type — the *element* type when
    ``container`` is ``LIST``, in which case the array profile applies
    regardless of the element kind. ``full`` includes everything in ``safe``.
    Nullable fields additionally get ``isnull`` (safe) and ``exists`` (full).
    Returns ``()`` for unsupported types. The ``regex`` config gate is
    applied by the caller, not here.
    """
    kind = type_kind(py_type)
    if kind is None:
        return ()
    tp = _ARRAY_PROFILE if container is Container.LIST else _PROFILES[kind]
    names = tp.safe if profile == "safe" else tp.safe + tp.full
    if nullable:
        names = names + (_NULLABLE_SAFE if profile == "safe" else _NULLABLE_SAFE + _NULLABLE_FULL)
    return names


def all_operators_for(
    py_type: Any, nullable: bool, container: Container = Container.SCALAR
) -> tuple[str, ...]:
    """Every operator name that is *valid* for a field (safe + full tiers).

    Used to validate explicit per-field operator configuration.
    """
    return operators_for(py_type, nullable, "full", container)
