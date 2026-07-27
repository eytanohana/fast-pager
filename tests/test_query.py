"""Tests for FilterQuery: AST construction, Mongo output, sorting, paging."""

from conftest import Color, User
from fast_pager import FilterConfig, FilterQuery, SortDirection
from fast_pager.params import build_plan


def make_query(raw: dict, config: FilterConfig | None = None) -> FilterQuery:
    plan = build_plan(User, config or FilterConfig())
    return FilterQuery(plan, plan.params_model.model_validate(raw))


def test_to_ast_builds_top_level_and_group():
    q = make_query({"age__gte": 21, "name": "alice", "sort": "-age", "limit": 10, "offset": 5})
    ast = q.to_ast()
    assert ast.where.op == "and"
    assert {(c.field, c.op) for c in ast.where.members} == {("age", "gte"), ("name", "eq")}
    assert ast.order_by[0].field == "age"
    assert ast.order_by[0].direction is SortDirection.DESC
    assert ast.page.limit == 10 and ast.page.offset == 5


def test_applied_lists_parsed_conditions():
    q = make_query({"age__in": ["1,2"]})
    (cond,) = q.applied
    assert cond.field == "age" and cond.op == "in" and cond.value == (1, 2)


def test_empty_request_yields_empty_query_and_defaults():
    q = make_query({})
    assert q.applied == ()
    assert q.to_mongo() == {}
    assert q.sort_mongo() == []
    assert q.limit == 50 and q.offset == 0 and q.skip == 0


def test_to_mongo_merges_same_field():
    q = make_query({"age__gte": 21, "age__lt": 65})
    assert q.to_mongo() == {"age": {"$gte": 21, "$lt": 65}}


def test_enum_values_compile_to_raw_values():
    q = make_query({"color": "red"})
    assert q.to_mongo() == {"color": "red"}
    assert q.applied[0].value is Color.RED


def test_sort_mongo_maps_directions():
    q = make_query({"sort": "-age, name"})
    assert q.sort_mongo() == [("age", -1), ("name", 1)]


def test_skip_and_limit_from_params():
    q = make_query({"limit": 7, "offset": 21})
    assert q.limit == 7 and q.skip == 21


def test_array_conditions_flow_through_the_ast():
    from conftest import Tagged

    plan = build_plan(Tagged, FilterConfig())
    q = FilterQuery(plan, plan.params_model.model_validate({"tags__has_any": ["a,b"]}))
    (cond,) = q.applied
    assert cond.field == "tags" and cond.op == "has_any" and cond.value == ("a", "b")
    assert q.to_mongo() == {"tags": {"$in": ["a", "b"]}}


def test_elem_conditions_flow_through_the_ast_with_the_marker():
    from conftest import Shopper

    plan = build_plan(Shopper, FilterConfig(default_profile="full"))
    q = FilterQuery(
        plan,
        plan.params_model.model_validate(
            {"orders__elem__amount__gte": 100, "orders__elem__status": "refunded"}
        ),
    )
    assert {(c.field, c.op) for c in q.applied} == {
        ("orders.$elem.amount", "gte"),
        ("orders.$elem.status", "eq"),
    }
    assert q.to_mongo() == {
        "orders": {"$elemMatch": {"amount": {"$gte": 100.0}, "status": "refunded"}}
    }


def test_map_conditions_compile_end_to_end():
    from conftest import Profile

    plan = build_plan(Profile, FilterConfig())
    q = FilterQuery(
        plan,
        plan.params_model.model_validate({"metadata__has_key": "region", "metadata__tier": "gold"}),
    )
    assert q.to_mongo() == {
        "metadata.region": {"$exists": True},
        "metadata.tier": "gold",
    }


def test_repr_is_informative():
    q = make_query({"age__gte": 21})
    text = repr(q)
    assert "User" in text and "gte" in text


def test_page_strategy_resolves_to_the_same_offset_window():
    q = make_query({"page": 3, "page_size": 20}, FilterConfig(pagination="page"))
    assert q.limit == 20
    assert q.offset == 40 and q.skip == 40
    assert q.to_ast().page.limit == 20 and q.to_ast().page.offset == 40


def test_page_strategy_defaults_resolve_to_the_first_page():
    q = make_query({}, FilterConfig(pagination="page"))
    assert q.limit == 50 and q.offset == 0 and q.skip == 0
