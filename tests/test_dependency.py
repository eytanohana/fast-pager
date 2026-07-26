"""Integration tests: a real FastAPI app via TestClient, OpenAPI included."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import Aliased, Curated, Customer, Tagged, User
from fast_pager import ConfigurationError, FilterConfig, FilterDepends, FilterQuery


def make_app(target=User, config=None) -> TestClient:
    app = FastAPI()

    @app.get("/items")
    def items(q: FilterQuery = FilterDepends(target, config=config)):
        return {
            "mongo": q.to_mongo(),
            "sort": q.sort_mongo(),
            "skip": q.skip,
            "limit": q.limit,
            "applied": [[c.field, c.op] for c in q.applied],
        }

    return TestClient(app)


def test_good_request_produces_expected_mongo_dict():
    client = make_app()
    r = client.get(
        "/items",
        params=[
            ("name", "alice"),
            ("age__gte", "21"),
            ("age__lt", "65"),
            ("nickname__isnull", "true"),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mongo"] == {
        "name": "alice",
        "age": {"$gte": 21, "$lt": 65},
        "nickname": None,
    }


def test_list_params_accept_repeated_and_comma_joined():
    client = make_app()
    r = client.get("/items", params=[("status__in", "active,trial"), ("status__in", "banned")])
    assert r.status_code == 200
    assert r.json()["mongo"] == {"status": {"$in": ["active", "trial", "banned"]}}


def test_sorting_and_pagination():
    client = make_app()
    r = client.get("/items", params={"sort": "-age,name", "limit": 20, "offset": 40})
    body = r.json()
    assert body["sort"] == [["age", -1], ["name", 1]]
    assert body["skip"] == 40 and body["limit"] == 20


def test_default_limit_applies():
    client = make_app()
    assert make_app().get("/items").json()["limit"] == 50
    r = client.get("/items", params={"limit": 30}, headers={})
    assert r.json()["limit"] == 30


@pytest.mark.parametrize(
    "params",
    [
        {"age__gte": "banana"},
        {"created_at__lt": "not-a-date"},
        {"uid__eq": "not-a-uuid"},
        {"status": "unknown-literal"},
        {"limit": "101"},
        {"limit": "0"},
        {"offset": "-1"},
        {"sort": "not_a_field"},
        {"age__between": "1"},
    ],
)
def test_bad_values_return_422(params):
    client = make_app(config=FilterConfig(default_profile="full"))
    assert client.get("/items", params=params).status_code == 422


def test_unknown_params_are_ignored():
    client = make_app()
    r = client.get("/items", params={"bogus__op": "x", "name": "a"})
    assert r.status_code == 200
    assert r.json()["applied"] == [["name", "eq"]]


def test_max_filters_limit_enforced_as_422():
    client = make_app(config=FilterConfig(max_filters=1))
    r = client.get("/items", params={"age__gte": 1, "age__lt": 2})
    assert r.status_code == 422


def test_filter_depends_accepts_filter_query_alias():
    client = make_app(target=FilterQuery[User])
    r = client.get("/items", params={"age__gte": 21})
    assert r.json()["mongo"] == {"age": {"$gte": 21}}


def test_filter_depends_rejects_bad_targets():
    with pytest.raises(ConfigurationError):
        FilterDepends(42)
    with pytest.raises(ConfigurationError):
        FilterDepends(FilterQuery[int])


def test_alias_used_in_params_and_compiled_query():
    client = make_app(target=Aliased)
    r = client.get("/items", params={"userName__contains": "an"})
    assert r.status_code == 200
    assert r.json()["mongo"] == {"userName": {"$regex": "an"}}


def test_config_error_at_registration_not_runtime():
    app = FastAPI()
    with pytest.raises(ConfigurationError, match=r"'contains'.*'age'"):

        @app.get("/items")
        def items(
            q: FilterQuery = FilterDepends(
                User, config=FilterConfig(operators={"age": ["contains"]})
            ),
        ):
            return {}


def test_openapi_shows_typed_filter_params():
    client = make_app()
    spec = client.get("/openapi.json").json()
    params = {p["name"]: p for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert params["age__gte"]["schema"]["anyOf"][0]["type"] == "integer"
    assert params["name__contains"]["schema"]["anyOf"][0]["type"] == "string"
    status_in = params["status__in"]["schema"]["anyOf"][0]
    assert status_in["type"] == "array"
    assert status_in["items"] == {"enum": ["active", "trial", "banned"], "type": "string"}
    assert params["nickname__isnull"]["schema"]["anyOf"][0]["type"] == "boolean"
    assert "substring" in params["name__contains"]["description"]
    assert params["limit"]["schema"]["default"] == 50
    assert params["limit"]["schema"]["maximum"] == 100
    assert params["offset"]["schema"]["minimum"] == 0
    assert "sort" in params
    # full-tier / gated operators are absent by default
    assert "name__regex" not in params
    assert "name__icontains" not in params


class Duplicated(BaseModel):
    name: str
    name__eq: str


def test_registration_collision_error_names_both_sources():
    with pytest.raises(ConfigurationError, match="collision"):
        make_app(target=Duplicated)


# ---------------------------------------------------------------------------
# Stage 2: strict unknown-param mode and source/param mapping end to end.
# ---------------------------------------------------------------------------

STRICT = FilterConfig(unknown_params="strict")


def test_strict_mode_rejects_unknown_filter_params_naming_them():
    client = make_app(config=STRICT)
    r = client.get("/items", params={"nmae__eq": "typo", "name": "a"})
    assert r.status_code == 422
    (error,) = r.json()["detail"]
    assert error["loc"] == ["query", "nmae__eq"]
    assert "nmae__eq" in error["msg"]


def test_strict_mode_rejects_known_field_with_ungenerated_operator():
    client = make_app(config=STRICT)
    # `regex` is gated off by default, so `name__regex` is not a generated
    # parameter — in strict mode that is a client error, not a silent no-op.
    assert client.get("/items", params={"name__regex": "^a"}).status_code == 422


def test_strict_mode_collects_every_offending_param():
    client = make_app(config=STRICT)
    r = client.get("/items", params={"a__eq": "1", "b__eq": "2"})
    assert r.status_code == 422
    assert {e["loc"][1] for e in r.json()["detail"]} == {"a__eq", "b__eq"}


def test_strict_mode_leaves_separator_free_params_alone():
    app = FastAPI()

    @app.get("/items")
    def items(verbose: bool = False, q: FilterQuery = FilterDepends(User, config=STRICT)):
        return {"verbose": verbose, "applied": [[c.field, c.op] for c in q.applied]}

    client = TestClient(app)
    r = client.get("/items", params={"verbose": "true", "name": "a", "unknown": "x"})
    assert r.status_code == 200
    assert r.json() == {"verbose": True, "applied": [["name", "eq"]]}


def test_strict_mode_accepts_a_fully_recognized_request():
    client = make_app(config=STRICT)
    r = client.get("/items", params={"name__contains": "a", "sort": "-age", "limit": 5})
    assert r.status_code == 200


def test_ignore_mode_stays_the_default():
    client = make_app()
    assert client.get("/items", params={"nmae__eq": "typo"}).status_code == 200


def test_source_and_param_mapping_compile_end_to_end():
    client = make_app(target=Curated)
    r = client.get(
        "/items",
        params={"age__gte": "21", "points__gte": "1.5", "sort": "-age,points"},
    )
    assert r.status_code == 200
    body = r.json()
    # `age` filters/sorts compile to the Mongo source name `ageYears`;
    # the public param `points` compiles to the model field `score`.
    assert body["mongo"] == {"ageYears": {"$gte": 21}, "score": {"$gte": 1.5}}
    assert body["sort"] == [["ageYears", -1], ["score", 1]]


def test_ops_none_params_do_not_exist_even_in_ignore_mode():
    client = make_app(target=Curated)
    r = client.get("/items", params={"ssn": "123-45-6789"})
    assert r.status_code == 200
    assert r.json()["applied"] == []


# ---------------------------------------------------------------------------
# Phase 3a: arrays of scalars end to end.
# ---------------------------------------------------------------------------


def test_array_request_produces_expected_mongo_dict():
    client = make_app(target=Tagged)
    r = client.get(
        "/items",
        params=[
            ("tags__has", "python"),
            ("scores__has_any", "1,2"),
            ("colors__has", "red"),
            ("labels__empty", "false"),
        ],
    )
    assert r.status_code == 200
    assert r.json()["mongo"] == {
        "tags": "python",
        "scores": {"$in": [1, 2]},
        "colors": "red",
        "labels.0": {"$exists": True},
    }


def test_array_empty_true_and_len_compile_end_to_end():
    client = make_app(target=Tagged, config=FilterConfig(default_profile="full"))
    r = client.get("/items", params={"tags__empty": "true", "scores__len__gte": "2"})
    assert r.status_code == 200
    assert r.json()["mongo"] == {
        "$and": [
            {"tags": {"$eq": []}},
            {
                "$expr": {
                    "$gte": [
                        {"$size": {"$cond": [{"$isArray": "$scores"}, "$scores", []]}},
                        2,
                    ]
                }
            },
        ]
    }


@pytest.mark.parametrize(
    "params",
    [
        {"scores__has": "banana"},
        {"tags__len__eq": "many"},
        {"tags__empty": "maybe"},
        {"colors__has": "chartreuse"},
        {"sort": "tags"},  # arrays are not sortable by default
    ],
)
def test_array_bad_values_return_422(params):
    client = make_app(target=Tagged)
    assert client.get("/items", params=params).status_code == 422


def test_array_max_list_length_enforced_as_422():
    client = make_app(target=Tagged, config=FilterConfig(max_list_length=2))
    assert client.get("/items", params={"tags__has_any": "a,b,c"}).status_code == 422
    assert client.get("/items", params={"tags__has_any": "a,b"}).status_code == 200


def test_strict_mode_rejects_scalar_operator_on_array_field():
    client = make_app(target=Tagged, config=FilterConfig(unknown_params="strict"))
    # No scalar operators on arrays (design doc 02): `tags__contains` is
    # never generated, so in strict mode it is a client error.
    assert client.get("/items", params={"tags__contains": "py"}).status_code == 422


def test_openapi_shows_typed_array_params():
    client = make_app(target=Tagged)
    spec = client.get("/openapi.json").json()
    params = {p["name"]: p for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert params["tags__has"]["schema"]["anyOf"][0]["type"] == "string"
    has_any = params["scores__has_any"]["schema"]["anyOf"][0]
    assert has_any["type"] == "array" and has_any["items"]["type"] == "integer"
    assert params["tags__len__eq"]["schema"]["anyOf"][0]["type"] == "integer"
    assert params["tags__empty"]["schema"]["anyOf"][0]["type"] == "boolean"
    assert "tags" not in params  # no bare-eq sugar for arrays
    assert "tags__contains" not in params
    assert "tags__len__gte" not in params  # full tier


# ---------------------------------------------------------------------------
# Phase 3b: nested Pydantic models end to end.
# ---------------------------------------------------------------------------


def test_nested_request_compiles_to_dotted_paths_and_merges_subdocuments():
    client = make_app(target=Customer)
    r = client.get(
        "/items",
        params=[
            ("address__city__contains", "ams"),
            ("address__geo__lat__gte", "1.5"),
            ("address__geo__lat__lt", "3.5"),
            ("billing__isnull", "false"),
        ],
    )
    assert r.status_code == 200
    assert r.json()["mongo"] == {
        "address.city": {"$regex": "ams"},
        "address.geo.lat": {"$gte": 1.5, "$lt": 3.5},
        "billing": {"$ne": None},
    }


def test_nested_array_and_source_rename_compile_end_to_end():
    client = make_app(target=Customer)
    r = client.get("/items", params={"address__tags__has": "home", "address__zip_code": "1012"})
    assert r.status_code == 200
    assert r.json()["mongo"] == {"address.tags": "home", "address.zip": "1012"}


def test_nested_sorting_compiles_to_the_dotted_source():
    client = make_app(target=Customer)
    r = client.get("/items", params={"sort": "-address__city,name"})
    assert r.status_code == 200
    assert r.json()["sort"] == [["address.city", -1], ["name", 1]]


def test_nested_bad_values_return_422():
    client = make_app(target=Customer)
    assert client.get("/items", params={"address__geo__lat__gte": "high"}).status_code == 422
    assert client.get("/items", params={"sort": "address"}).status_code == 422  # embedding
    assert client.get("/items", params={"sort": "address__tags"}).status_code == 422  # array


def test_strict_mode_rejects_params_beyond_the_depth_bound():
    class L3(BaseModel):
        x: int

    class L2(BaseModel):
        l3: L3

    class L1(BaseModel):
        l2: L2

    class Root(BaseModel):
        l1: L1

    client = make_app(target=Root, config=FilterConfig(unknown_params="strict"))
    assert client.get("/items", params={"l1__l2__l3__x": "1"}).status_code == 422


def test_nested_subtree_exclusion_end_to_end():
    client = make_app(target=Customer, config=FilterConfig(exclude=["billing"]))
    r = client.get("/items", params={"billing__city": "x", "name": "a"})
    assert r.status_code == 200
    assert r.json()["applied"] == [["name", "eq"]]


def test_openapi_shows_typed_nested_params():
    client = make_app(target=Customer)
    spec = client.get("/openapi.json").json()
    params = {p["name"]: p for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert params["address__city__contains"]["schema"]["anyOf"][0]["type"] == "string"
    assert params["address__geo__lat__gte"]["schema"]["anyOf"][0]["type"] == "number"
    assert params["billing__isnull"]["schema"]["anyOf"][0]["type"] == "boolean"
    assert params["address__tags__has"]["schema"]["anyOf"][0]["type"] == "string"
    assert "address__geo__lat" in params  # bare-eq sugar for nested leaves
    assert "address" not in params  # non-nullable embedding: no params of its own
    assert "address__isnull" not in params


def test_openapi_reflects_param_rename_and_curated_ops():
    client = make_app(target=Curated)
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert {"points", "points__gte", "name__contains"} <= params
    assert "score" not in params and "ssn" not in params
    assert "name__startswith" not in params  # outside the exact ops list
