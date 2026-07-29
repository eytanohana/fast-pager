"""The backend conformance suite (design doc 04).

A **fixed battery** of ``FilterAST`` inputs plus a runner that checks the
backend-neutral semantics every adapter must honor:

- every case whose operators and structural features the adapter *declares*
  (``supported_ops`` / ``capabilities``) must compile **without error**;
- every case it does *not* declare must raise
  :class:`~fast_pager.errors.CompilationError` **naming the operator** and
  the backend — a filter is never silently dropped;
- cases marked ``invalid`` (e.g. unsafe map keys) must raise
  :class:`~fast_pager.errors.CompilationError` on every backend that
  supports their operators;
- where the adapter author supplies an **expected output** for a case, the
  compiled result must match it.

The battery fixes the *inputs*; the expected *outputs* are backend-specific
and supplied by each adapter's own test suite. Any adapter — first-party or
community — runs this suite to claim compatibility. Typical pytest wiring::

    import pytest
    from fast_pager.conformance import CASES, run_case

    EXPECTED = {"scalar-eq": ..., "scalar-gte": ..., ...}  # my backend's shapes

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
    def test_conformance(case):
        run_case(MyCompiler(), case, expected=EXPECTED.get(case.id, UNCHECKED))

Pass ``compare=`` when your compiled output does not support plain ``==``
(e.g. SQLAlchemy expressions: compare rendered SQL). ``run_battery`` is the
one-call convenience over the whole battery.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .ast import Condition, Group, PageSpec, Sort, SortDirection
from .backends.base import Capability, QueryCompiler, capabilities_for_path
from .errors import CompilationError

__all__ = [
    "CASES",
    "UNCHECKED",
    "ConformanceCase",
    "is_supported",
    "run_battery",
    "run_case",
]


class _Unchecked:
    """Sentinel type: no expected output supplied for a case."""

    def __repr__(self) -> str:
        return "UNCHECKED"


UNCHECKED = _Unchecked()
"""Sentinel: run the case's semantic assertions without an expected output."""


@dataclass(frozen=True)
class ConformanceCase:
    """One fixed input of the battery, with its semantic requirements.

    ``ops`` and ``requires`` describe what the input *needs* from a backend;
    the runner derives pass/fail behavior from them against the compiler's
    declarations. ``invalid`` inputs must be rejected by every backend.
    """

    id: str
    kind: Literal["where", "order", "page"]
    description: str = ""
    where: Group | None = None
    order: tuple[Sort, ...] = ()
    page: PageSpec | None = None
    ops: frozenset[str] = frozenset()
    requires: frozenset[Capability] = frozenset()
    invalid: bool = False


def _collect_ops(group: Group) -> frozenset[str]:
    ops: set[str] = set()
    for member in group.members:
        if isinstance(member, Group):
            ops |= _collect_ops(member)
        else:
            ops.add(member.op)
    return frozenset(ops)


def _collect_requires(group: Group) -> frozenset[Capability]:
    needed: set[Capability] = set()
    for member in group.members:
        if isinstance(member, Group):
            needed |= _collect_requires(member)
        else:
            needed |= capabilities_for_path(member.field)
    return frozenset(needed)


def _where(
    id: str,
    *members: Condition | Group,
    op: Literal["and", "or"] = "and",
    invalid: bool = False,
    description: str = "",
) -> ConformanceCase:
    """Build a ``where`` case, deriving ``ops``/``requires`` from the AST."""
    group = Group(op=op, members=members)
    return ConformanceCase(
        id=id,
        kind="where",
        description=description,
        where=group,
        ops=_collect_ops(group),
        requires=_collect_requires(group),
        invalid=invalid,
    )


def _c(field: str, op: str, value: Any) -> Condition:
    return Condition(field=field, op=op, value=value)


#: A value exercising both escaping regimes: ``.`` is a regex metacharacter
#: (Mongo must ``re.escape`` it), ``%``/``_`` are LIKE wildcards (SQL must
#: escape them). Backends must match it as a *literal* substring.
_TRICKY = "a.b%_c"

CASES: tuple[ConformanceCase, ...] = (
    # ------------------------------------------------------------------ #
    # Scalars                                                            #
    # ------------------------------------------------------------------ #
    _where("scalar-eq", _c("age", "eq", 21)),
    _where("scalar-ne", _c("age", "ne", 21)),
    _where("scalar-gt", _c("age", "gt", 21)),
    _where("scalar-gte", _c("age", "gte", 21)),
    _where("scalar-lt", _c("age", "lt", 21)),
    _where("scalar-lte", _c("age", "lte", 21)),
    _where("scalar-in", _c("age", "in", (1, 2))),
    _where("scalar-nin", _c("age", "nin", (1, 2))),
    _where("scalar-between", _c("age", "between", (21, 65))),
    # Escaping/anchoring: the value must match literally; `startswith` is
    # anchored to the start, `endswith` to the end.
    _where("string-contains", _c("name", "contains", _TRICKY)),
    _where("string-icontains", _c("name", "icontains", _TRICKY)),
    _where("string-startswith", _c("name", "startswith", _TRICKY)),
    _where("string-istartswith", _c("name", "istartswith", _TRICKY)),
    _where("string-endswith", _c("name", "endswith", _TRICKY)),
    _where("string-iendswith", _c("name", "iendswith", _TRICKY)),
    # `regex` is the explicit pattern operator: the value IS the pattern.
    _where("string-regex", _c("name", "regex", "^a.*b$")),
    _where("string-text-search", _c("name", "text_search", "hello world")),
    _where("null-isnull-true", _c("nickname", "isnull", True)),
    _where("null-isnull-false", _c("nickname", "isnull", False)),
    _where("null-exists-true", _c("nickname", "exists", True)),
    _where("null-exists-false", _c("nickname", "exists", False)),
    # ------------------------------------------------------------------ #
    # Grouping and same-field merging                                    #
    # ------------------------------------------------------------------ #
    _where(
        "merge-same-field-range",
        _c("age", "gte", 21),
        _c("age", "lt", 65),
        description="two range conditions on one field combine (AND)",
    ),
    _where(
        "merge-eq-with-range",
        _c("age", "eq", 5),
        _c("age", "lt", 10),
    ),
    _where(
        "merge-conflicting-string-ops",
        _c("name", "startswith", "a"),
        _c("name", "endswith", "b"),
        _c("age", "gte", 1),
        description="same-field conditions that cannot share one clause still AND together",
    ),
    _where("group-empty", description="an empty AND group matches everything"),
    _where(
        "group-or",
        _c("a", "eq", 1),
        _c("b", "gt", 2),
        op="or",
    ),
    _where(
        "group-or-nested-in-and",
        _c("a", "eq", 1),
        Group(op="or", members=(_c("b", "eq", 2), _c("c", "eq", 3))),
    ),
    _where(
        "group-and-nested-in-or",
        _c("a", "eq", 1),
        Group(op="and", members=(_c("b", "eq", 2), _c("c", "eq", 3))),
        op="or",
    ),
    # ------------------------------------------------------------------ #
    # Arrays of scalars                                                  #
    # ------------------------------------------------------------------ #
    _where("array-has", _c("tags", "has", "python")),
    _where("array-has-any", _c("tags", "has_any", ("a", "b"))),
    _where("array-has-all", _c("tags", "has_all", ("a", "b"))),
    _where("array-len-eq", _c("tags", "len__eq", 3)),
    _where("array-len-ne", _c("tags", "len__ne", 3)),
    _where("array-len-gt", _c("tags", "len__gt", 2)),
    _where("array-len-gte", _c("tags", "len__gte", 2)),
    _where("array-len-lt", _c("tags", "len__lt", 2)),
    _where("array-len-lte", _c("tags", "len__lte", 2)),
    _where("array-empty-true", _c("tags", "empty", True)),
    _where("array-empty-false", _c("tags", "empty", False)),
    _where(
        "array-len-range-pair",
        _c("tags", "len__gte", 2),
        _c("tags", "len__lt", 5),
        description="two guarded length ranges must both apply",
    ),
    _where(
        "array-len-with-membership",
        _c("tags", "has", "x"),
        _c("tags", "len__gt", 1),
    ),
    # ------------------------------------------------------------------ #
    # Dotted nested paths (requires NESTED_PATHS)                        #
    # ------------------------------------------------------------------ #
    _where("nested-eq", _c("address.city", "eq", "ams")),
    _where(
        "nested-merge-range",
        _c("address.geo.lat", "gte", 1.0),
        _c("address.geo.lat", "lt", 2.0),
    ),
    # ------------------------------------------------------------------ #
    # `$elem` grouping (requires ELEM_MATCH): all conditions on one array #
    # in one AND group hold for the SAME element (doc 02 adapter contract)#
    # ------------------------------------------------------------------ #
    _where(
        "elem-two-conditions-one-array",
        _c("orders.$elem.amount", "gte", 100),
        _c("orders.$elem.status", "eq", "refunded"),
        description="same-element semantics: one element-match construct",
    ),
    _where(
        "elem-same-field-merge",
        _c("orders.$elem.amount", "gte", 100),
        _c("orders.$elem.amount", "lt", 500),
    ),
    _where(
        "elem-two-arrays",
        _c("orders.$elem.status", "eq", "paid"),
        _c("returns.$elem.status", "eq", "open"),
        _c("name", "eq", "a"),
        description="distinct array fields each get their own element match",
    ),
    _where(
        "elem-with-shape-condition",
        _c("orders", "len__eq", 2),
        _c("orders.$elem.status", "eq", "paid"),
    ),
    _where(
        "elem-relative-nested-path",
        _c("orders.$elem.supplier.name", "eq", "acme"),
    ),
    _where(
        "elem-nested-hops",
        _c("orders.$elem.items.$elem.sku", "eq", "x-1"),
        _c("orders.$elem.items.$elem.qty", "gte", 2),
        _c("orders.$elem.status", "eq", "paid"),
    ),
    _where(
        "elem-lone-in-or",
        _c("orders.$elem.status", "eq", "paid"),
        _c("a", "eq", 1),
        op="or",
    ),
    # ------------------------------------------------------------------ #
    # Maps                                                               #
    # ------------------------------------------------------------------ #
    _where("map-has-key", _c("metadata", "has_key", "region")),
    _where("map-has-key-in-elem", _c("orders.$elem.meta", "has_key", "gift")),
    _where(
        "map-has-key-unsafe-dot",
        _c("metadata", "has_key", "a.b"),
        invalid=True,
        description="map keys become path segments; metacharacters must be rejected",
    ),
    _where("map-has-key-unsafe-dollar", _c("metadata", "has_key", "$where"), invalid=True),
    _where("map-has-key-empty", _c("metadata", "has_key", ""), invalid=True),
    # ------------------------------------------------------------------ #
    # Capability-based rejection: an operator no registry defines. Every  #
    # backend must reject it loudly, naming the operator.                 #
    # ------------------------------------------------------------------ #
    _where("unknown-operator", _c("a", "frobnicate", 1)),
    # ------------------------------------------------------------------ #
    # Sort and paging                                                    #
    # ------------------------------------------------------------------ #
    ConformanceCase(
        id="order-two-keys",
        kind="order",
        order=(Sort("age", SortDirection.DESC), Sort("name", SortDirection.ASC)),
    ),
    ConformanceCase(id="order-empty", kind="order"),
    ConformanceCase(id="page-window", kind="page", page=PageSpec(limit=10, offset=30)),
)
"""The fixed battery. Inputs (and ids) are stable within a minor release."""


def is_supported(compiler: QueryCompiler, case: ConformanceCase) -> bool:
    """Whether the compiler declares everything ``case`` needs."""
    return case.ops <= compiler.supported_ops and case.requires <= compiler.capabilities


def _fail(case: ConformanceCase, message: str) -> None:
    detail = f" ({case.description})" if case.description else ""
    raise AssertionError(f"conformance case {case.id!r}{detail}: {message}")


def _check_expected(
    case: ConformanceCase,
    result: Any,
    expected: Any,
    compare: Callable[[Any, Any], bool] | None,
) -> None:
    if isinstance(expected, _Unchecked):
        return
    equal = compare(result, expected) if compare is not None else bool(result == expected)
    if not equal:
        _fail(
            case, f"compiled output does not match expected.\n got: {result!r}\n want: {expected!r}"
        )


def run_case(
    compiler: QueryCompiler,
    case: ConformanceCase,
    expected: Any = UNCHECKED,
    *,
    compare: Callable[[Any, Any], bool] | None = None,
) -> None:
    """Run one battery case against ``compiler``; raise ``AssertionError`` on failure.

    ``expected`` is the backend-specific compiled shape for the case (omit or
    pass :data:`UNCHECKED` to assert only the semantic behavior). ``compare``
    overrides plain ``==`` for outputs without value equality.
    """
    if case.kind == "order":
        _check_expected(case, compiler.compile_order(list(case.order)), expected, compare)
        return
    if case.kind == "page":
        assert case.page is not None
        _check_expected(case, compiler.compile_page(case.page), expected, compare)
        return
    assert case.where is not None
    if case.invalid:
        # Invalid inputs (e.g. unsafe map keys) must be rejected by every
        # backend — whether because the op is unsupported or because the
        # compiler validates the value.
        try:
            result = compiler.compile_where(case.where)
        except CompilationError:
            return
        _fail(case, f"expected CompilationError for an invalid input, got {result!r}")
    if not is_supported(compiler, case):
        missing_ops = case.ops - compiler.supported_ops
        missing_caps = case.requires - compiler.capabilities
        try:
            result = compiler.compile_where(case.where)
        except CompilationError as exc:
            message = str(exc)
            if missing_ops and not any(op in message for op in missing_ops):
                _fail(
                    case,
                    f"the CompilationError must name the unsupported operator "
                    f"({', '.join(sorted(missing_ops))}); got: {message}",
                )
            return
        _fail(
            case,
            f"backend {compiler.name!r} does not declare "
            f"{sorted(o for o in missing_ops) + sorted(c.value for c in missing_caps)} "
            f"but compiled the filter anyway (never silently drop a filter); got {result!r}",
        )
    try:
        result = compiler.compile_where(case.where)
    except CompilationError as exc:
        _fail(
            case,
            f"backend {compiler.name!r} declares support for "
            f"{sorted(case.ops)} but failed to compile: {exc}",
        )
    _check_expected(case, result, expected, compare)


def run_battery(
    compiler: QueryCompiler,
    expected: Mapping[str, Any] | None = None,
    *,
    compare: Callable[[Any, Any], bool] | None = None,
    cases: Iterable[ConformanceCase] = CASES,
) -> None:
    """Run every battery case against ``compiler`` (see :func:`run_case`).

    ``expected`` maps case ids to backend-specific compiled shapes; ids not
    present are run with semantic assertions only. Unknown ids in
    ``expected`` are an error (they usually mean a typo'd case id).
    """
    table = dict(expected or {})
    known = {case.id for case in cases}
    unknown = set(table) - known
    if unknown:
        raise AssertionError(f"expected-output table names unknown case ids: {sorted(unknown)}")
    for case in cases:
        run_case(compiler, case, table.get(case.id, UNCHECKED), compare=compare)
