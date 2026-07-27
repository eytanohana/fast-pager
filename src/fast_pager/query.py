"""`FilterQuery`: the uniform, parsed result object handed to endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from .ast import Condition, FilterAST, Group, PageSpec, Sort, SortDirection
from .backends.mongo import MongoCompiler
from .operators import Arity
from .pagination import Page, TotalMode, paginate_collection

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
    - :attr:`applied` — the parsed conditions, for introspection;
    - :meth:`paginate` — run the query against a Mongo-like collection and
      return a :class:`~fast_pager.Page` envelope.
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
        """The effective page size (client value, bounded by ``max_limit``).

        Under ``FilterConfig(pagination="page")`` this is the ``page_size``
        parameter — the two strategies resolve to the same window.
        """
        if self._plan.config.pagination == "page":
            return int(getattr(self._raw, "page_size"))
        return int(getattr(self._raw, "limit"))

    @property
    def offset(self) -> int:
        """Number of items skipped before the page starts.

        Under ``FilterConfig(pagination="page")`` this is computed from the
        1-based ``page`` parameter: ``(page - 1) * page_size``.
        """
        if self._plan.config.pagination == "page":
            return (int(getattr(self._raw, "page")) - 1) * self.limit
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
            page=PageSpec(limit=self.limit, offset=self.offset),
        )

    def to_mongo(self) -> dict[str, Any]:
        """Compile the filter conditions to a plain MongoDB query dict."""
        return _DEFAULT_COMPILER.compile_where(self.to_ast().where)

    def sort_mongo(self) -> list[tuple[str, int]]:
        """Compile the sort keys to ``[(field, 1|-1), ...]`` for ``sort()``."""
        return _DEFAULT_COMPILER.compile_order(list(self._sorts))

    async def paginate(self, collection: Any, *, total: TotalMode = "exact") -> Page[Any]:
        """Run the find (+ optional count) and return a :class:`~fast_pager.Page`.

        ``collection`` is duck-typed against the standard Mongo collection
        surface — ``find(filter)`` returning a cursor with ``sort``/``skip``/
        ``limit``, ``count_documents(filter)``, ``estimated_document_count()``
        — with awaitables detected at runtime, so motor and pymongo (sync and
        async) collections all work and no driver is ever imported. The
        filter, sort keys, and pagination window are exactly this query's
        :meth:`to_mongo`, :meth:`sort_mongo`, :attr:`skip`, and :attr:`limit`.

        ``total`` prices the count explicitly (design doc 01):

        - ``"exact"`` (default) — ``count_documents`` with the same filter;
          correct but costly on large collections.
        - ``"estimated"`` — the cheap, metadata-based
          ``estimated_document_count()``, which is only meaningful for an
          *unfiltered* query; when the compiled filter is non-empty (or the
          collection has no such method) it **falls back to an exact count**.
        - ``"none"`` — skip counting; ``Page.total`` is ``None`` (the right
          default for infinite-scroll UIs).

        An object without the expected surface raises :class:`TypeError`
        naming what is missing.
        """
        return await paginate_collection(
            collection,
            where=self.to_mongo(),
            order=self.sort_mongo(),
            limit=self.limit,
            offset=self.offset,
            total=total,
        )

    def __repr__(self) -> str:
        """Debug representation showing the applied filter conditions."""
        return (
            f"FilterQuery(model={self._plan.model.__name__}, applied={list(self._conditions)!r}, "
            f"sort={list(self._sorts)!r}, limit={self.limit}, offset={self.offset})"
        )
