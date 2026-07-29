"""The backend adapter contract: :class:`QueryCompiler` + capability model.

A compiler turns the backend-neutral :class:`~fast_pager.ast.FilterAST` parts
into a backend query. It is the *only* component that knows about a specific
database, and it declares what it can compile — ``supported_ops`` for
operators and ``capabilities`` for structural features — so unsupported
configuration can be rejected before the app serves traffic and an
unsupported AST is rejected loudly at compile time. We never silently drop a
filter (design doc 04).
"""

from __future__ import annotations

import enum
from typing import Any, Protocol, runtime_checkable

from ..ast import ELEM_SOURCE_MARKER, Group, PageSpec, Sort

__all__ = ["Capability", "QueryCompiler", "capabilities_for_path"]

#: An `elem` boundary inside a dotted source path, e.g. "orders.$elem.amount".
_ELEM_BOUNDARY = f".{ELEM_SOURCE_MARKER}."


class Capability(enum.Enum):
    """Structural features a backend may or may not be able to express.

    Operators are declared per-name in ``QueryCompiler.supported_ops``;
    capabilities cover the *shape* features that are not a single operator.
    The set is deliberately minimal and honest — it models exactly what
    varies between the shipped backends today:

    - ``NESTED_PATHS`` — dotted source paths into embedded documents
      (``"address.city"``). Mongo: native dot notation. SQLAlchemy: JSON
      column access (the root column must be JSON-typed).
    - ``ELEM_MATCH`` — same-element matching for ``list[NestedModel]``
      fields: grouping all conditions sharing a ``.$elem.`` path prefix into
      one element-match construct (Mongo ``$elemMatch``). Generic SQL has no
      equivalent, so the SQLAlchemy backend rejects ``$elem`` paths.
    """

    NESTED_PATHS = "nested_paths"
    ELEM_MATCH = "elem_match"


def capabilities_for_path(source: str) -> frozenset[Capability]:
    """The :class:`Capability` set a condition's source path requires.

    A path carrying the ``.$elem.`` marker requires ``ELEM_MATCH``; a path
    that is dotted *outside* the marker (``"address.city"``, or the
    element-relative ``"supplier.name"`` inside an elem hop) additionally
    requires ``NESTED_PATHS``. Flat paths require nothing.
    """
    parts = source.split(_ELEM_BOUNDARY)
    needed = set()
    if len(parts) > 1:
        needed.add(Capability.ELEM_MATCH)
    if any("." in part for part in parts):
        needed.add(Capability.NESTED_PATHS)
    return frozenset(needed)


@runtime_checkable
class QueryCompiler(Protocol):
    """Protocol every backend adapter implements."""

    name: str
    """Human-readable adapter name (e.g. ``"mongo"``)."""

    supported_ops: frozenset[str]
    """Operator names this adapter can compile; the capability declaration.

    Compiling a :class:`~fast_pager.ast.Condition` whose ``op`` is not in
    this set must raise :class:`~fast_pager.errors.CompilationError` naming
    the operator and the backend — never silently drop it.
    """

    capabilities: frozenset[Capability]
    """Structural features this adapter can express (see :class:`Capability`).

    Compiling a condition whose source path requires a missing capability
    (:func:`capabilities_for_path`) must raise
    :class:`~fast_pager.errors.CompilationError`.
    """

    def compile_where(self, group: Group) -> Any:
        """Compile a filter group into the backend's query representation."""
        ...

    def compile_order(self, order: list[Sort]) -> Any:
        """Compile sort keys into the backend's ordering representation."""
        ...

    def compile_page(self, page: PageSpec) -> Any:
        """Compile the pagination window into the backend's representation."""
        ...
