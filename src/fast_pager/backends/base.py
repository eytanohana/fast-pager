"""The backend adapter contract: :class:`QueryCompiler`.

A compiler turns the backend-neutral :class:`~fast_pager.ast.FilterAST` parts
into a backend query. It is the *only* component that knows about a specific
database, and it declares which operators it can compile so unsupported
configuration can be rejected before the app serves traffic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..ast import Group, Page, Sort

__all__ = ["QueryCompiler"]


@runtime_checkable
class QueryCompiler(Protocol):
    """Protocol every backend adapter implements."""

    name: str
    """Human-readable adapter name (e.g. ``"mongo"``)."""

    supported_ops: frozenset[str]
    """Operator names this adapter can compile; the capability declaration."""

    def compile_where(self, group: Group) -> Any:
        """Compile a filter group into the backend's query representation."""
        ...

    def compile_order(self, order: list[Sort]) -> Any:
        """Compile sort keys into the backend's ordering representation."""
        ...

    def compile_page(self, page: Page) -> Any:
        """Compile the pagination window into the backend's representation."""
        ...
