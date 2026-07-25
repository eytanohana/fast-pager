"""Parametrized operator → Mongo-dict table tests for MongoCompiler."""

import pytest

from fast_pager.ast import Condition, Group, Page, Sort, SortDirection
from fast_pager.backends.base import QueryCompiler
from fast_pager.backends.mongo import MongoCompiler
from fast_pager.errors import CompilationError
from fast_pager.operators import DEFAULT_REGISTRY

compiler = MongoCompiler()


def where(*conds):
    return compiler.compile_where(Group(op="and", members=conds))


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (Condition("age", "eq", 21), {"age": 21}),
        (Condition("age", "ne", 21), {"age": {"$ne": 21}}),
        (Condition("age", "gt", 21), {"age": {"$gt": 21}}),
        (Condition("age", "gte", 21), {"age": {"$gte": 21}}),
        (Condition("age", "lt", 21), {"age": {"$lt": 21}}),
        (Condition("age", "lte", 21), {"age": {"$lte": 21}}),
        (Condition("age", "in", (1, 2)), {"age": {"$in": [1, 2]}}),
        (Condition("age", "nin", (1, 2)), {"age": {"$nin": [1, 2]}}),
        (Condition("age", "between", (21, 65)), {"age": {"$gte": 21, "$lte": 65}}),
        (Condition("name", "contains", "a.b"), {"name": {"$regex": "a\\.b"}}),
        (
            Condition("name", "icontains", "a.b"),
            {"name": {"$regex": "a\\.b", "$options": "i"}},
        ),
        (Condition("name", "startswith", "a.b"), {"name": {"$regex": "^a\\.b"}}),
        (
            Condition("name", "istartswith", "a.b"),
            {"name": {"$regex": "^a\\.b", "$options": "i"}},
        ),
        (Condition("name", "endswith", "a.b"), {"name": {"$regex": "a\\.b$"}}),
        (
            Condition("name", "iendswith", "a.b"),
            {"name": {"$regex": "a\\.b$", "$options": "i"}},
        ),
        # regex is the explicit pattern operator: the value is NOT escaped.
        (Condition("name", "regex", "^a.*b$"), {"name": {"$regex": "^a.*b$"}}),
        (Condition("name", "text_search", "hello"), {"$text": {"$search": "hello"}}),
        (Condition("nickname", "isnull", True), {"nickname": None}),
        (Condition("nickname", "isnull", False), {"nickname": {"$ne": None}}),
        (Condition("nickname", "exists", True), {"nickname": {"$exists": True}}),
        (Condition("nickname", "exists", False), {"nickname": {"$exists": False}}),
    ],
)
def test_operator_table(condition, expected):
    assert where(condition) == expected


def test_supported_ops_covers_the_registry():
    assert MongoCompiler.supported_ops == frozenset(DEFAULT_REGISTRY)


def test_conforms_to_query_compiler_protocol():
    assert isinstance(compiler, QueryCompiler)


def test_same_field_conditions_merge_into_one_subdocument():
    result = where(
        Condition("age", "gte", 21),
        Condition("age", "lt", 65),
        Condition("name", "contains", "x"),
    )
    assert result == {"age": {"$gte": 21, "$lt": 65}, "name": {"$regex": "x"}}


def test_eq_merged_with_other_ops_keeps_dollar_eq():
    assert where(Condition("age", "eq", 5), Condition("age", "lt", 10)) == {
        "age": {"$eq": 5, "$lt": 10}
    }


def test_conflicting_same_operator_falls_back_to_and():
    result = where(
        Condition("name", "startswith", "a"),
        Condition("name", "endswith", "b"),
        Condition("age", "gte", 1),
    )
    assert result == {
        "$and": [
            {"name": {"$regex": "^a"}, "age": {"$gte": 1}},
            {"name": {"$regex": "b$"}},
        ]
    }


def test_single_conflicting_clause_without_base_is_unwrapped():
    result = compiler.compile_where(
        Group(op="and", members=(Group(op="and", members=(Condition("a", "eq", 1),)),))
    )
    assert result == {"a": 1}


def test_or_group_compiles_to_dollar_or():
    result = compiler.compile_where(
        Group(op="or", members=(Condition("a", "eq", 1), Condition("b", "gt", 2)))
    )
    assert result == {"$or": [{"a": 1}, {"b": {"$gt": 2}}]}


def test_nested_group_inside_and():
    result = compiler.compile_where(
        Group(
            op="and",
            members=(
                Condition("a", "eq", 1),
                Group(op="or", members=(Condition("b", "eq", 2), Condition("c", "eq", 3))),
            ),
        )
    )
    assert result == {"$and": [{"a": 1}, {"$or": [{"b": 2}, {"c": 3}]}]}


def test_unknown_operator_raises_compilation_error():
    with pytest.raises(CompilationError, match="frobnicate"):
        where(Condition("a", "frobnicate", 1))


def test_compile_order_and_page():
    order = [Sort("age", SortDirection.DESC), Sort("name", SortDirection.ASC)]
    assert compiler.compile_order(order) == [("age", -1), ("name", 1)]
    assert compiler.compile_page(Page(limit=10, offset=30)) == {"skip": 30, "limit": 10}


def test_or_group_member_that_is_a_group():
    result = compiler.compile_where(
        Group(
            op="or",
            members=(
                Condition("a", "eq", 1),
                Group(op="and", members=(Condition("b", "eq", 2), Condition("c", "eq", 3))),
            ),
        )
    )
    assert result == {"$or": [{"a": 1}, {"b": 2, "c": 3}]}
