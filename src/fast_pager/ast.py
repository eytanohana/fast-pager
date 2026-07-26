"""The backend-neutral filter AST.

Pure, frozen data structures — the contract between the HTTP/Pydantic front
half of the library and the database back half. This module imports nothing
from the rest of the package.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "ELEM_SOURCE_MARKER",
    "Condition",
    "FilterAST",
    "Group",
    "Page",
    "Sort",
    "SortDirection",
]

#: The path segment marking a `list[NestedModel]` element boundary inside a
#: Condition's dotted ``field`` path: ``"orders.$elem.amount"`` means "the
#: ``amount`` of an element of the ``orders`` array". Part of the AST
#: contract — backends group same-array `elem` conditions into their
#: element-match construct (Mongo: one ``$elemMatch`` per array field).
#: Mongo forbids ``$``-prefixed field names, so the marker can never collide
#: with a real document key.
ELEM_SOURCE_MARKER = "$elem"


class SortDirection(enum.Enum):
    """Sort direction for a single sort key (values match Mongo's 1/-1)."""

    ASC = 1
    DESC = -1


@dataclass(frozen=True)
class Condition:
    """A single filter condition: ``field <op> value``.

    ``field`` is the backend source path (e.g. ``"address.city"``), ``op`` is
    an operator name from the registry (``"gte"``, ``"contains"``, ...), and
    ``value`` is already coerced and validated by Pydantic.
    """

    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Group:
    """A boolean combination of conditions and sub-groups.

    v1 only ever *produces* a single top-level ``"and"`` group, but the node
    already models ``"or"`` as the extension point for boolean groups later.
    """

    op: Literal["and", "or"]
    members: tuple[Condition | Group, ...]


@dataclass(frozen=True)
class Sort:
    """One sort key: a backend source field path and a direction."""

    field: str
    direction: SortDirection


@dataclass(frozen=True)
class Page:
    """Offset/limit pagination window."""

    limit: int
    offset: int


@dataclass(frozen=True)
class FilterAST:
    """The complete, backend-agnostic result of parsing one request.

    Combines the ``where`` group, the ``order_by`` sort keys, and the
    pagination window. Plain data: trivially testable, serializable, loggable.
    """

    where: Group
    order_by: tuple[Sort, ...] = ()
    page: Page = field(default_factory=lambda: Page(limit=50, offset=0))
