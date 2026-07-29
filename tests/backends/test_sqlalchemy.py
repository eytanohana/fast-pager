"""SQLAlchemyCompiler: conformance battery, unit behavior, real execution."""

import datetime
import enum
from decimal import Decimal
from typing import Any, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from fast_pager import Capability, FilterConfig, FilterQuery
from fast_pager.ast import Condition, Group, Sort, SortDirection
from fast_pager.backends.base import QueryCompiler
from fast_pager.backends.sqlalchemy import SQLAlchemyCompiler, infer_model
from fast_pager.conformance import CASES, UNCHECKED, is_supported, run_case
from fast_pager.errors import CompilationError, ConfigurationError
from fast_pager.params import build_plan


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "things"
    id: Mapped[int] = mapped_column(primary_key=True)
    a: Mapped[int]
    b: Mapped[int]
    c: Mapped[int]
    age: Mapped[int]
    name: Mapped[str]
    nickname: Mapped[Optional[str]]
    address: Mapped[dict[str, Any]] = mapped_column(sa.JSON)


core_table = sa.Table(
    "core_things",
    sa.MetaData(),
    sa.Column("age", sa.Integer),
    sa.Column("name", sa.String),
)

compiler = SQLAlchemyCompiler(Thing)


def _sql(compiled):
    """Render a compiled construct to comparable literal SQLite SQL."""
    if compiled is None:
        return None
    if isinstance(compiled, list):
        return [_sql(element) for element in compiled]
    if isinstance(compiled, dict):
        return compiled
    return str(compiled.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))


def _compare(got, expected):
    return _sql(got) == expected


def where(*conds, op="and"):
    return compiler.compile_where(Group(op=op, members=conds))


# --------------------------------------------------------------------------- #
# The conformance battery, with the SQLAlchemy backend's locked SQL shapes.   #
# --------------------------------------------------------------------------- #

SQLALCHEMY_EXPECTED = {
    "scalar-eq": "things.age = 21",
    "scalar-ne": "things.age != 21",
    "scalar-gt": "things.age > 21",
    "scalar-gte": "things.age >= 21",
    "scalar-lt": "things.age < 21",
    "scalar-lte": "things.age <= 21",
    "scalar-in": "things.age IN (1, 2)",
    "scalar-nin": "(things.age NOT IN (1, 2))",
    "scalar-between": "things.age BETWEEN 21 AND 65",
    # autoescape: %/_ in the user value match literally (ESCAPE '/').
    "string-contains": "things.name LIKE '%' || 'a.b/%/_c' || '%' ESCAPE '/'",
    "string-icontains": "lower(things.name) LIKE '%' || lower('a.b/%/_c') || '%' ESCAPE '/'",
    "string-startswith": "things.name LIKE 'a.b/%/_c' || '%' ESCAPE '/'",
    "string-istartswith": "lower(things.name) LIKE lower('a.b/%/_c') || '%' ESCAPE '/'",
    "string-endswith": "things.name LIKE '%' || 'a.b/%/_c' ESCAPE '/'",
    "string-iendswith": "lower(things.name) LIKE '%' || lower('a.b/%/_c') ESCAPE '/'",
    "null-isnull-true": "things.nickname IS NULL",
    "null-isnull-false": "things.nickname IS NOT NULL",
    "merge-same-field-range": "things.age >= 21 AND things.age < 65",
    "merge-eq-with-range": "things.age = 5 AND things.age < 10",
    "merge-conflicting-string-ops": (
        "(things.name LIKE 'a' || '%' ESCAPE '/') AND "
        "(things.name LIKE '%' || 'b' ESCAPE '/') AND things.age >= 1"
    ),
    "group-empty": None,
    "group-or": "things.a = 1 OR things.b > 2",
    "group-or-nested-in-and": "things.a = 1 AND (things.b = 2 OR things.c = 3)",
    "group-and-nested-in-or": "things.a = 1 OR things.b = 2 AND things.c = 3",
    "nested-eq": "JSON_EXTRACT(things.address, '$.\"city\"') = 'ams'",
    "nested-merge-range": (
        'JSON_EXTRACT(things.address, \'$."geo"."lat"\') >= 1.0 AND '
        'JSON_EXTRACT(things.address, \'$."geo"."lat"\') < 2.0'
    ),
    "order-two-keys": ["things.age DESC", "things.name ASC"],
    "order-empty": [],
    "page-window": {"limit": 10, "offset": 30},
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_sqlalchemy_conformance(case):
    run_case(compiler, case, SQLALCHEMY_EXPECTED.get(case.id, UNCHECKED), compare=_compare)


def test_expected_table_covers_every_supported_case():
    needed = {case.id for case in CASES if not case.invalid and is_supported(compiler, case)}
    assert needed == set(SQLALCHEMY_EXPECTED)


# --------------------------------------------------------------------------- #
# Declarations and rejection behavior                                          #
# --------------------------------------------------------------------------- #


def test_conforms_to_query_compiler_protocol():
    assert isinstance(compiler, QueryCompiler)


def test_capabilities_declare_nested_but_not_elem():
    assert compiler.capabilities == frozenset({Capability.NESTED_PATHS})


@pytest.mark.parametrize(
    "op",
    ["regex", "text_search", "exists", "has", "has_any", "has_all", "len__eq", "empty", "has_key"],
)
def test_unsupported_operator_raises_naming_operator_and_backend(op):
    with pytest.raises(CompilationError, match=rf"sqlalchemy.*{op}|{op}.*sqlalchemy"):
        where(Condition("name", op, "x"))


def test_elem_path_is_rejected_loudly():
    with pytest.raises(CompilationError, match=r"elem_match"):
        where(Condition("orders.$elem.amount", "gte", 100))


def test_unknown_column_raises_with_available_columns():
    with pytest.raises(CompilationError, match=r"unknown column 'missing'.*Thing.*age"):
        where(Condition("missing", "eq", 1))


def test_dotted_path_on_non_json_column_is_rejected():
    with pytest.raises(CompilationError, match=r"'name'.*JSON"):
        where(Condition("name.city", "eq", "x"))


def test_constructor_rejects_non_sqlalchemy_targets():
    with pytest.raises(ConfigurationError, match="ORM mapped class or a Table"):
        SQLAlchemyCompiler(object())


# --------------------------------------------------------------------------- #
# Column resolution and JSON value coercion                                    #
# --------------------------------------------------------------------------- #


def test_core_table_columns_resolve():
    core = SQLAlchemyCompiler(core_table)
    expr = core.compile_where(Group(op="and", members=(Condition("age", "gte", 21),)))
    assert _sql(expr) == "core_things.age >= 21"


class Color(enum.Enum):
    RED = "red"


# On SQLite the as_integer/as_float/as_boolean coercions are Python-side
# (json_extract already returns native values), so no CAST appears in the
# rendered SQL; on PostgreSQL the same expressions render with CASTs over
# `->>`. The value-side normalization (bool -> 1, enum -> value, datetime ->
# ISO string, Decimal -> float) is dialect-independent and visible below.
@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (
            Condition("address.floor", "eq", 3),
            "JSON_EXTRACT(things.address, '$.\"floor\"') = 3",
        ),
        # as_boolean: `== True` compares against 1, not the Python literal.
        (
            Condition("address.active", "eq", True),
            "JSON_EXTRACT(things.address, '$.\"active\"') = 1",
        ),
        (
            Condition("address.lat", "gte", 1.5),
            "JSON_EXTRACT(things.address, '$.\"lat\"') >= 1.5",
        ),
        (
            Condition("address.price", "lte", Decimal("9.5")),
            "JSON_EXTRACT(things.address, '$.\"price\"') <= 9.5",
        ),
        # Temporals compare as ISO-8601 strings inside JSON documents.
        (
            Condition("address.since", "gte", datetime.datetime(2026, 1, 2, 3, 4, 5)),
            "JSON_EXTRACT(things.address, '$.\"since\"') >= '2026-01-02T03:04:05'",
        ),
        # Enums compare by value.
        (
            Condition("address.color", "eq", Color.RED),
            "JSON_EXTRACT(things.address, '$.\"color\"') = 'red'",
        ),
        # List values coerce per element; the probe is the first element.
        (
            Condition("address.floor", "in", (1, 2)),
            "JSON_EXTRACT(things.address, '$.\"floor\"') IN (1, 2)",
        ),
        # An empty list has no probe: compare as string (matches nothing).
        (
            Condition("address.city", "in", ()),
            "JSON_EXTRACT(things.address, '$.\"city\"') IN (SELECT 1 FROM (SELECT 1) WHERE 1!=1)",
        ),
        # isnull on a JSON element uses the string form (missing key -> NULL).
        (
            Condition("address.city", "isnull", True),
            "JSON_EXTRACT(things.address, '$.\"city\"') IS NULL",
        ),
        # String operators on JSON elements.
        (
            Condition("address.city", "contains", "am"),
            "(JSON_EXTRACT(things.address, '$.\"city\"')) LIKE '%' || 'am' || '%' ESCAPE '/'",
        ),
        (
            Condition("address.floor", "between", (1, 3)),
            "JSON_EXTRACT(things.address, '$.\"floor\"') BETWEEN 1 AND 3",
        ),
    ],
)
def test_json_element_coercion_table(condition, expected):
    assert _sql(where(condition)) == expected


def test_empty_nested_group_is_vacuously_true_inside_or():
    expr = compiler.compile_where(
        Group(op="or", members=(Condition("a", "eq", 1), Group(op="and", members=())))
    )
    # or_(x, true()) short-circuits to TRUE — correct: the empty AND group
    # constrains nothing.
    assert _sql(expr) == "1 = 1"


def test_sort_on_json_path_uses_the_extracted_element():
    (expr,) = compiler.compile_order([Sort("address.city", SortDirection.ASC)])
    # as_string: plain json_extract, not the JSON_QUOTE()d representation.
    assert _sql(expr) == "JSON_EXTRACT(things.address, '$.\"city\"') ASC"


# --------------------------------------------------------------------------- #
# infer_model                                                                  #
# --------------------------------------------------------------------------- #


def test_infer_model_from_orm_select():
    assert infer_model(sa.select(Thing)) is Thing


def test_infer_model_from_core_select():
    assert infer_model(sa.select(core_table)) is core_table


def test_infer_model_rejects_multi_entity_statements():
    class Other(Base):
        __tablename__ = "others"
        id: Mapped[int] = mapped_column(primary_key=True)

    with pytest.raises(CompilationError, match="pass the model explicitly"):
        infer_model(sa.select(Thing, Other))


def test_infer_model_rejects_entityless_statements():
    with pytest.raises(CompilationError, match="pass the model explicitly"):
        infer_model(sa.select(sa.literal(1)))


# --------------------------------------------------------------------------- #
# End-to-end: FilterQuery -> SQLAlchemy -> real SQLite execution               #
# --------------------------------------------------------------------------- #

from pydantic import BaseModel  # noqa: E402


class AddressModel(BaseModel):
    city: str


class ThingModel(BaseModel):
    name: str
    age: int
    nickname: Optional[str] = None
    address: AddressModel


@pytest.fixture(scope="module")
def session():
    engine = sa.create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    rows = [
        Thing(a=0, b=0, c=0, age=20, name="Ana", nickname=None, address={"city": "ams"}),
        Thing(a=0, b=0, c=0, age=35, name="bob 100%", nickname="b", address={"city": "nyc"}),
        Thing(a=0, b=0, c=0, age=50, name="carol 1000", nickname=None, address={"city": "ams"}),
    ]
    with Session(engine) as sess:
        sess.add_all(rows)
        sess.commit()
        yield sess


def make_query(raw: dict) -> FilterQuery:
    plan = build_plan(ThingModel, FilterConfig(default_profile="full"))
    return FilterQuery(plan, plan.params_model.model_validate(raw))


def run_names(session, q):
    stmt = q.apply_sqlalchemy(sa.select(Thing))
    return [row.name for row in session.execute(stmt).scalars()]


def test_apply_sqlalchemy_executes_filters(session):
    assert run_names(session, make_query({"age__gte": 30})) == ["bob 100%", "carol 1000"]


def test_like_escaping_matches_literal_percent(session):
    # `%` in user input is literal: matches "bob 100%" but not "carol 1000".
    assert run_names(session, make_query({"name__contains": "100%"})) == ["bob 100%"]


def test_icontains_is_case_insensitive(session):
    assert run_names(session, make_query({"name__icontains": "ANA"})) == ["Ana"]


def test_json_nested_filter_executes(session):
    assert run_names(session, make_query({"address__city": "ams"})) == ["Ana", "carol 1000"]


def test_isnull_executes(session):
    assert run_names(session, make_query({"nickname__isnull": True})) == ["Ana", "carol 1000"]


def test_sort_and_window_execute(session):
    q = make_query({"sort": "-age", "limit": 2, "offset": 1})
    assert run_names(session, q) == ["bob 100%", "Ana"]


def test_apply_sqlalchemy_with_explicit_model(session):
    q = make_query({"age__lt": 30})
    stmt = q.apply_sqlalchemy(sa.select(Thing), model=Thing)
    assert [row.name for row in session.execute(stmt).scalars()] == ["Ana"]


def test_to_sqlalchemy_and_sort_sqlalchemy_surface():
    q = make_query({"age__gte": 21, "sort": "-age"})
    assert _sql(q.to_sqlalchemy(Thing)) == "things.age >= 21"
    assert _sql(q.sort_sqlalchemy(Thing)) == ["things.age DESC"]
    assert q.to_sqlalchemy(Thing) is not None


def test_to_sqlalchemy_empty_query_is_none():
    q = make_query({})
    assert q.to_sqlalchemy(Thing) is None
    assert q.sort_sqlalchemy(Thing) == []
