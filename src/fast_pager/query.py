"""`FilterQuery`: the uniform, parsed result object handed to endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from .ast import Condition, FilterAST, Group, Page, Sort, SortDirection
from .backends.mongo import MongoCompiler
from .operators import Arity

if TYPE_CHECKING:
    from .params import QueryPlan

__all__ = ["FilterQuery"]

ModelT = TypeVar("ModelT", bound=BaseModel)

_DEFAULT_COMPILER = MongoCompiler()


class FilterQuery(Generic[ModelT]):
    """The parsed, validated filter/sort/pagination state of one request.

    Regardless of how it was declared, every request yields this same object:

    - :meth:`to_ast` — the backend-agnostic :class:`~fast_pager.ast.FilterAST`;
    - :meth:`to_mongo` — a plain dict ready for ``find()``;
    - :meth:`sort_mongo` — ``[(field, 1|-1), ...]`` ready for ``sort()``;
    - :attr:`skip` / :attr:`limit` — pagination ints;
    - :attr:`applied` — the parsed conditions, for introspection.
    """

    def __init__(self, plan: QueryPlan, raw: BaseModel) -> None:
        """Build from the registration-time plan and the validated raw params."""
        self._plan = plan
        self._raw = raw
        self._conditions = self._parse_conditions()
        self._sorts = self._parse_sorts()

    def _parse_conditions(self) -> tuple[Condition, ...]:
        conditions: list[Condition] = []
        for p in self._plan.params:
            value = getattr(self._raw, p.python_name)
            if value is None:
                continue
            if p.operator.arity in (Arity.LIST, Arity.RANGE):
                value = tuple(value)
            conditions.append(Condition(field=p.spec.source, op=p.operator.name, value=value))
        return tuple(conditions)

    def _parse_sorts(self) -> tuple[Sort, ...]:
        raw_sort: str | None = getattr(self._raw, "sort")
        if not raw_sort:
            return ()
        sorts: list[Sort] = []
        for token in raw_sort.split(","):
            token = token.strip()
            direction = SortDirection.DESC if token.startswith("-") else SortDirection.ASC
            name = token[1:] if token.startswith("-") else token
            sorts.append(Sort(field=self._plan.sources.get(name, name), direction=direction))
        return tuple(sorts)

    @property
    def applied(self) -> tuple[Condition, ...]:
        """The parsed, validated filter conditions applied by this request."""
        return self._conditions

    @property
    def limit(self) -> int:
        """The effective page size (client value, bounded by ``max_limit``)."""
        return int(getattr(self._raw, "limit"))

    @property
    def offset(self) -> int:
        """Number of items skipped before the page starts."""
        return int(getattr(self._raw, "offset"))

    @property
    def skip(self) -> int:
        """Alias for :attr:`offset`, matching Mongo's ``skip`` vocabulary."""
        return self.offset

    def to_ast(self) -> FilterAST:
        """The backend-agnostic AST for this request (a top-level AND group)."""
        return FilterAST(
            where=Group(op="and", members=self._conditions),
            order_by=self._sorts,
            page=Page(limit=self.limit, offset=self.offset),
        )

    def to_mongo(self) -> dict[str, Any]:
        """Compile the filter conditions to a plain MongoDB query dict."""
        return _DEFAULT_COMPILER.compile_where(self.to_ast().where)

    def sort_mongo(self) -> list[tuple[str, int]]:
        """Compile the sort keys to ``[(field, 1|-1), ...]`` for ``sort()``."""
        return _DEFAULT_COMPILER.compile_order(list(self._sorts))

    def __repr__(self) -> str:
        """Debug representation showing the applied filter conditions."""
        return (
            f"FilterQuery(model={self._plan.model.__name__}, applied={list(self._conditions)!r}, "
            f"sort={list(self._sorts)!r}, limit={self.limit}, offset={self.offset})"
        )
