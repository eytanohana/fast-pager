"""Integration tests: a real FastAPI app via TestClient, OpenAPI included."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import Aliased, Curated, User
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


def test_openapi_reflects_param_rename_and_curated_ops():
    client = make_app(target=Curated)
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert {"points", "points__gte", "name__contains"} <= params
    assert "score" not in params and "ssn" not in params
    assert "name__startswith" not in params  # outside the exact ops list
