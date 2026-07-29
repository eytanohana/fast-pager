"""SQLAlchemy 2.0 query compiler (``pip install 'fast-pager[sqlalchemy]'``).

Compiles the neutral AST into SQLAlchemy constructs: ``Condition`` →
``ColumnElement[bool]`` boolean expressions, ``Group`` → ``and_``/``or_``,
``Sort`` → ``asc()``/``desc()`` order-by expressions, ``PageSpec`` →
``{"limit": ..., "offset": ...}``. This module is the only place SQLAlchemy
is imported; the core stays dependency-free (importing *this module* without
SQLAlchemy installed raises a clear ``ImportError``).

**Substring escaping.** ``contains``/``startswith``/``endswith`` (and the
``i*`` variants) compile via SQLAlchemy's ``autoescape`` LIKE helpers, so
``%``, ``_``, and the escape character in user input are escaped — the value
matches as a **literal** substring/prefix/suffix, mirroring the Mongo
compiler's ``re.escape()`` guarantee.

**Case sensitivity.** The ``i*`` variants compile to ``ILIKE`` on PostgreSQL
and ``lower(col) LIKE lower(pattern)`` elsewhere — reliably
case-insensitive. The plain variants compile to ``LIKE``, whose sensitivity
follows the database: case-sensitive on PostgreSQL, but **SQLite's LIKE is
case-insensitive for ASCII by default** (``PRAGMA case_sensitive_like``
flips it) and MySQL/MariaDB follow the column collation (usually
insensitive). If you need guaranteed-insensitive matching, expose the ``i*``
operators; the plain operators are "database-native LIKE".

**Nested paths (JSON columns).** A dotted source path (``"address.city"``)
resolves its root segment to a column, which must be JSON-typed
(``sa.JSON`` or a subclass such as PostgreSQL ``JSONB``); the remaining
segments become JSON path access (SQLite ``json_extract``, PostgreSQL
``->``/``->>``). The JSON element is CAST for the comparison based on the
condition value's type (``as_string``/``as_integer``/``as_float``/
``as_boolean``), datetimes compare as ISO-8601 strings (which order
correctly), and enums compare by value. Tested on SQLite; PostgreSQL uses
the same generic SQLAlchemy JSON operators. A dotted path whose root column
is *not* JSON-typed raises :class:`~fast_pager.errors.CompilationError`
(relationship JOINs are a planned follow-up, design doc 04).

**Declared unsupported** (rejected with a loud
:class:`~fast_pager.errors.CompilationError`, never silently dropped):

- array operators (``has``/``has_any``/``has_all``/``len__*``/``empty``) —
  generic SQL has no portable array/JSON-array predicate set;
- ``$elem`` element matching (``list[NestedModel]``) — no ``$elemMatch``
  equivalent; we do not fake same-element semantics;
- ``has_key`` — JSON key existence is dialect-specific (JSONB ``?`` vs
  ``json_extract`` null-vs-missing ambiguity);
- ``regex`` — LIKE-free pattern matching is dialect-specific (``~`` vs
  ``REGEXP``; SQLite has none by default);
- ``text_search`` — no generic SQL full-text query;
- ``exists`` — SQL columns always exist; use ``isnull``.
"""

from __future__ import annotations

import datetime
import enum
import operator as py_operator
from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

try:
    import sqlalchemy as sa
except ModuleNotFoundError as exc:  # pragma: no cover — dev/test envs install it
    raise ImportError(
        "the fast-pager SQLAlchemy backend requires SQLAlchemy >= 2.0; "
        "install it with: pip install 'fast-pager[sqlalchemy]'"
    ) from exc

from ..ast import ELEM_SOURCE_MARKER, Condition, Group, PageSpec, Sort, SortDirection
from ..errors import CompilationError, ConfigurationError
from .base import Capability

__all__ = ["SQLAlchemyCompiler", "infer_model"]

#: An `elem` boundary inside a dotted source path: "orders.$elem.amount".
_ELEM = f".{ELEM_SOURCE_MARKER}."

_STRING_OPS = frozenset(
    {"contains", "icontains", "startswith", "istartswith", "endswith", "iendswith"}
)

_COMPARISONS: dict[str, Callable[[Any, Any], Any]] = {
    "eq": py_operator.eq,
    "ne": py_operator.ne,
    "gt": py_operator.gt,
    "gte": py_operator.ge,
    "lt": py_operator.lt,
    "lte": py_operator.le,
}


def infer_model(statement: Any) -> Any:
    """Infer the single model/table a ``select()`` statement is built over.

    Used by :meth:`FilterQuery.apply_sqlalchemy` when no explicit model is
    given: an ORM ``select(User)`` yields ``User``; a Core
    ``select(table)`` yields the :class:`~sqlalchemy.Table`. Statements over
    several entities (joins, multi-entity selects) are ambiguous and raise
    :class:`~fast_pager.errors.CompilationError` asking for an explicit
    ``model=``.
    """
    entities = {
        described["entity"]
        for described in statement.column_descriptions
        if described.get("entity") is not None
    }
    if len(entities) == 1:
        return next(iter(entities))
    if not entities:
        tables = {from_ for from_ in statement.get_final_froms() if isinstance(from_, sa.Table)}
        if len(tables) == 1:
            return next(iter(tables))
    raise CompilationError(
        "cannot infer the model from the statement (it selects from "
        f"{len(entities) or 'no'} entities); pass the model explicitly: "
        "q.apply_sqlalchemy(stmt, model=...)"
    )


def _json_scalar(value: Any) -> Any:
    """Normalize one value for comparison against a JSON element."""
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        # JSON documents store temporals as ISO-8601 strings, which compare
        # and order correctly as strings.
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class SQLAlchemyCompiler:
    """Compiles :class:`~fast_pager.ast.FilterAST` parts to SQLAlchemy constructs.

    ``model`` is the SQLAlchemy side of the pair (design doc 04): an ORM
    mapped class (including SQLModel classes) or a Core
    :class:`~sqlalchemy.Table`, whose columns the AST's source paths resolve
    against. Values pass through to SQLAlchemy's type system unchanged
    (enum members stay enum members for ``sa.Enum`` columns); only JSON-path
    comparisons normalize values (see the module docstring).
    """

    name = "sqlalchemy"

    capabilities: frozenset[Capability] = frozenset({Capability.NESTED_PATHS})
    """Dotted nested paths are expressible (via JSON columns); ``$elem``
    same-element matching is not — SQL has no generic ``$elemMatch``."""

    supported_ops: frozenset[str] = frozenset(
        {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "in",
            "nin",
            "between",
            "contains",
            "icontains",
            "startswith",
            "istartswith",
            "endswith",
            "iendswith",
            "isnull",
        }
    )

    def __init__(self, model: Any) -> None:
        """Bind the compiler to one mapped class or table."""
        try:
            inspected = sa.inspect(model)
            columns = inspected.columns
        except (sa.exc.NoInspectionAvailable, AttributeError) as exc:
            raise ConfigurationError(
                f"SQLAlchemyCompiler expects an ORM mapped class or a Table, got {model!r}"
            ) from exc
        self._model = model
        self._model_name = getattr(model, "__name__", None) or str(getattr(model, "name", model))
        self._columns: dict[str, sa.ColumnElement[Any]] = dict(columns.items())

    # ------------------------------------------------------------------ #
    # QueryCompiler protocol                                             #
    # ------------------------------------------------------------------ #

    def compile_where(self, group: Group) -> sa.ColumnElement[bool] | None:
        """Compile a filter group to one boolean expression, or ``None``.

        ``None`` means "no WHERE clause" (the empty top-level group); an
        empty *nested* group compiles to ``true()`` so boolean semantics
        hold inside ``or_``.
        """
        exprs = [self._member(member) for member in group.members]
        if not exprs:
            return None
        if len(exprs) == 1:
            return exprs[0]
        return sa.and_(*exprs) if group.op == "and" else sa.or_(*exprs)

    def compile_order(self, order: list[Sort]) -> list[sa.ColumnElement[Any]]:
        """Compile sort keys to ``asc()``/``desc()`` expressions for ``order_by``.

        A dotted (JSON-path) sort key sorts by the extracted element in the
        dialect's native representation (``as_string`` — plain
        ``json_extract`` on SQLite, ``->>`` on PostgreSQL). Note the dialect
        caveat: on PostgreSQL ``->>`` yields *text*, so numeric JSON values
        sort lexicographically — promote hot numeric sort keys to real
        columns.
        """
        keys: list[sa.ColumnElement[Any]] = []
        for sort in order:
            element = self._element(sort.field)
            if "." in sort.field:
                comparator = cast("sa.JSON.Comparator[Any]", element.comparator)
                element = cast("Callable[[], sa.ColumnElement[Any]]", comparator.as_string)()
            keys.append(
                sa.asc(element) if sort.direction is SortDirection.ASC else sa.desc(element)
            )
        return keys

    def compile_page(self, page: PageSpec) -> dict[str, int]:
        """Compile the pagination window to ``{"limit": ..., "offset": ...}``."""
        return {"limit": page.limit, "offset": page.offset}

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _member(self, member: Condition | Group) -> sa.ColumnElement[bool]:
        if isinstance(member, Group):
            compiled = self.compile_where(member)
            # An empty nested group is vacuously true (it constrains nothing).
            return sa.true() if compiled is None else compiled
        return self._fragment(member)

    def _column(self, name: str) -> sa.ColumnElement[Any]:
        column = self._columns.get(name)
        if column is None:
            raise CompilationError(
                f"unknown column {name!r} on {self._model_name} for backend "
                f"'sqlalchemy'; available columns: {', '.join(sorted(self._columns))}"
            )
        return column

    def _element(self, field: str) -> sa.ColumnElement[Any]:
        """Resolve a source path to a column or raw JSON element expression."""
        if _ELEM in field:
            raise CompilationError(
                f"SQLAlchemyCompiler does not support element matching "
                f"(capability 'elem_match') required by field {field!r}: "
                f"list[NestedModel] '$elem' filtering has no generic SQL "
                f"equivalent; exclude these parameters for this backend"
            )
        root, _, rest = field.partition(".")
        column = self._column(root)
        if not rest:
            return column
        if not isinstance(column.type, sa.JSON):
            raise CompilationError(
                f"dotted path {field!r} on backend 'sqlalchemy' requires the root "
                f"column {root!r} of {self._model_name} to be JSON-typed "
                f"(sa.JSON/JSONB), got {column.type!r}; map the nested model to a "
                f"JSON column or exclude the nested parameters"
            )
        segments = rest.split(".")
        # Tuple indexing renders one JSON path expression ('$."geo"."lat"')
        # instead of nesting extractions per segment.
        index = segments[0] if len(segments) == 1 else tuple(segments)
        element: sa.ColumnElement[Any] = column[index]
        return element

    def _target(self, field: str, op: str, value: Any) -> tuple[sa.ColumnElement[Any], Any]:
        """Resolve ``field`` and adapt ``value``, CASTing JSON elements."""
        element = self._element(field)
        if "." not in field:
            if isinstance(value, tuple):
                value = list(value)
            return element, value
        # JSON element: normalize the value and CAST the element to match.
        if isinstance(value, (list, tuple)):
            value = [_json_scalar(item) for item in value]
            probe = value[0] if value else None
        else:
            value = probe = _json_scalar(value)
        if op in _STRING_OPS or op == "isnull" or probe is None:
            kind = "string"
        elif isinstance(probe, bool):
            kind = "boolean"
        elif isinstance(probe, int):
            kind = "integer"
        elif isinstance(probe, float):
            kind = "float"
        else:
            kind = "string"
        comparator = cast("sa.JSON.Comparator[Any]", element.comparator)
        # The as_* helpers are untyped upstream; cast the bound method once.
        as_kind = cast("Callable[[], sa.ColumnElement[Any]]", getattr(comparator, f"as_{kind}"))
        return as_kind(), value

    def _fragment(self, cond: Condition) -> sa.ColumnElement[bool]:
        op = cond.op
        if op not in self.supported_ops:
            raise CompilationError(
                f"SQLAlchemyCompiler (backend 'sqlalchemy') does not support "
                f"operator {op!r} (field {cond.field!r})"
            )
        element, value = self._target(cond.field, op, cond.value)
        comparison = _COMPARISONS.get(op)
        if comparison is not None:
            # The comparison operators' stubs return Any on ColumnElement[Any];
            # they all build ColumnElement[bool] binary expressions.
            return cast("sa.ColumnElement[bool]", comparison(element, value))
        if op == "in":
            return element.in_(value)
        if op == "nin":
            return element.not_in(value)
        if op == "between":
            low, high = value
            return element.between(low, high)
        if op == "isnull":
            return element.is_(None) if value else element.is_not(None)
        # The LIKE family: autoescape makes %/_ in user input literal.
        text = str(value)
        if op == "contains":
            return element.contains(text, autoescape=True)
        if op == "icontains":
            return element.icontains(text, autoescape=True)
        if op == "startswith":
            return element.startswith(text, autoescape=True)
        if op == "istartswith":
            return element.istartswith(text, autoescape=True)
        if op == "endswith":
            return element.endswith(text, autoescape=True)
        return element.iendswith(text, autoescape=True)
