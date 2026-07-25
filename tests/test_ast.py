"""Tests for the frozen AST dataclasses."""

import dataclasses

import pytest

from fast_pager.ast import Condition, FilterAST, Group, Page, Sort, SortDirection


def test_condition_is_frozen():
    cond = Condition(field="age", op="gte", value=21)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cond.value = 22


def test_group_holds_conditions_and_groups():
    inner = Group(op="or", members=(Condition("a", "eq", 1),))
    outer = Group(op="and", members=(Condition("b", "eq", 2), inner))
    assert outer.members[1] is inner


def test_sort_direction_values_match_mongo():
    assert SortDirection.ASC.value == 1
    assert SortDirection.DESC.value == -1


def test_filter_ast_defaults():
    ast = FilterAST(where=Group(op="and", members=()))
    assert ast.order_by == ()
    assert ast.page == Page(limit=50, offset=0)


def test_filter_ast_composition():
    ast = FilterAST(
        where=Group(op="and", members=(Condition("age", "gte", 21),)),
        order_by=(Sort("age", SortDirection.DESC),),
        page=Page(limit=10, offset=20),
    )
    assert ast.where.members[0].field == "age"
    assert ast.page.offset == 20
