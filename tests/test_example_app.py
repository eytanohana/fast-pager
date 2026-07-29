"""Tests for `examples/`: the example apps stay runnable.

The Mongo example is a plain script directory (not a package), so it is
imported by putting its directory on ``sys.path`` — the same import a user
gets from ``uvicorn main:app`` inside ``examples/mongo_app``. The SQLAlchemy
example is also a ``main.py``, so it is loaded under a distinct module name
via ``importlib`` to avoid clashing in ``sys.modules``.
"""

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(_EXAMPLES / "mongo_app"))

from main import app  # noqa: E402  (path setup must run first)


def _load_sqlalchemy_app():
    spec = importlib.util.spec_from_file_location(
        "sqlalchemy_example_main", _EXAMPLES / "sqlalchemy_app" / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's postponed annotations
    # (`from __future__ import annotations`) resolve against its namespace.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.app


client = TestClient(app)
sql_client = TestClient(_load_sqlalchemy_app())


def test_zero_config_endpoint_compiles_scalars_and_filterable_metadata():
    r = client.get(
        "/users",
        params={"name__contains": "ana", "age__gte": "21", "email": "a@b.co", "sort": "-age"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["filter"] == {
        "name": {"$regex": "ana"},
        "age": {"$gte": 21},
        "email": "a@b.co",
    }
    assert body["sort"] == [["age", -1]]
    # `Filterable(ops=["eq"])` on email: nothing beyond exact match exists…
    assert client.get("/users", params={"email__contains": "b"}).json()["filter"] == {}
    # …and `ops.NONE` on password_hash generates nothing at all.
    assert client.get("/users", params={"password_hash": "x"}).json()["filter"] == {}


def test_zero_config_endpoint_covers_the_compound_types():
    r = client.get(
        "/users",
        params={
            "tags__has": "python",
            "address__city": "Amsterdam",
            "address__zip_code": "1012",
            "metadata__region": "emea",
            "orders__len__eq": "2",
        },
    )
    assert r.json()["filter"] == {
        "tags": "python",
        "address.city": "Amsterdam",
        "address.zip": "1012",  # Filterable(source="zip") on the nested field
        "metadata.region": "emea",
        "orders": {"$size": 2},
    }


def test_public_endpoint_is_a_strict_allow_list():
    r = client.get("/public/users", params={"name__startswith": "al", "tags__has": "python"})
    assert r.status_code == 200
    assert r.json()["filter"] == {"name": {"$regex": "^al"}, "tags": "python"}
    # Unlisted fields and operators are simply not part of the surface.
    assert client.get("/public/users", params={"email": "a@b.co"}).json()["filter"] == {}
    assert client.get("/public/users", params={"name__endswith": "x"}).json()["filter"] == {}


def test_admin_endpoint_elem_matching_and_custom_filter():
    r = client.get(
        "/admin/users",
        params={
            "orders__elem__status": "paid",
            "orders__elem__amount__gte": "100",
            "active_since": "2026-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 200
    assert r.json()["filter"] == {
        "orders": {"$elemMatch": {"amount": {"$gte": 100.0}, "status": "paid"}},
        # the datetime value was parsed and re-serialized by the response model
        "last_login": {"$gte": "2026-01-01T00:00:00Z"},
    }
    # the custom filter's value really is typed as the target field
    assert client.get("/admin/users", params={"active_since": "not-a-date"}).status_code == 422


def test_admin_endpoint_is_strict_about_unknown_params():
    assert client.get("/admin/users", params={"nmae__eq": "typo"}).status_code == 422
    # The public surface's default mode just ignores the same typo.
    assert client.get("/public/users", params={"nmae__eq": "typo"}).status_code == 200


def test_admin_sortable_allow_list_and_limits():
    r = client.get("/admin/users", params={"sort": "-last_login", "limit": "500"})
    assert r.status_code == 200
    assert r.json()["sort"] == [["last_login", -1]] and r.json()["limit"] == 500
    assert client.get("/admin/users", params={"sort": "email"}).status_code == 422
    assert client.get("/public/users", params={"limit": "500"}).status_code == 422


def test_openapi_documents_all_three_surfaces():
    spec = client.get("/openapi.json").json()

    def names(path):
        return {p["name"] for p in spec["paths"][path]["get"]["parameters"]}

    assert "password_hash" not in {n.split("__")[0] for n in names("/users")}
    assert names("/public/users") == {
        "name__contains",
        "name__startswith",
        "age__gte",
        "age__lte",
        "tags__has",
        "address__city",
        "address__city__eq",
        "limit",
        "offset",
        "sort",
    }
    admin = names("/admin/users")
    assert {"active_since", "orders__elem__status", "metadata__has_key"} <= admin
    admin_params = {p["name"]: p for p in spec["paths"]["/admin/users"]["get"]["parameters"]}
    assert "last login" in admin_params["active_since"]["description"]


# ---------------------------------------------------------------------------
# The SQLAlchemy example: the same endpoints, actually executing on SQLite.
# ---------------------------------------------------------------------------


def test_sqlalchemy_app_filters_and_sorts_real_rows():
    r = sql_client.get("/users", params={"age__gte": "30", "sort": "-age"})
    assert r.status_code == 200
    body = r.json()
    assert [item["name"] for item in body["items"]] == ["Anatoly", "Ana"]
    assert body["total"] == 2


def test_sqlalchemy_app_nested_json_filter_executes():
    r = sql_client.get("/users", params={"address__city": "Amsterdam"})
    assert {item["name"] for item in r.json()["items"]} == {"Ana", "Bram"}
    # The renamed nested source (`zip_code` -> JSON key `zip`) works too.
    r = sql_client.get("/users", params={"address__zip_code": "1017"})
    assert [item["name"] for item in r.json()["items"]] == ["Bram"]


def test_sqlalchemy_app_isnull_and_string_operators():
    r = sql_client.get("/users", params={"last_login__isnull": "true"})
    assert [item["name"] for item in r.json()["items"]] == ["Anatoly"]
    r = sql_client.get("/users", params={"name__startswith": "Ana"})
    assert {item["name"] for item in r.json()["items"]} == {"Ana", "Anatoly"}


def test_sqlalchemy_app_pagination_window():
    r = sql_client.get("/users", params={"sort": "name", "limit": "1", "offset": "1"})
    body = r.json()
    assert [item["name"] for item in body["items"]] == ["Anatoly"]
    assert body["total"] == 3 and body["limit"] == 1 and body["offset"] == 1


def test_sqlalchemy_app_filterable_metadata_still_applies():
    # email is eq-only; password_hash generates nothing (ops.NONE).
    spec = sql_client.get("/openapi.json").json()
    names = {p["name"] for p in spec["paths"]["/users"]["get"]["parameters"]}
    assert "email" in names and "email__contains" not in names
    assert not any(name.startswith("password_hash") for name in names)


def test_sqlalchemy_app_public_surface_is_a_strict_allow_list():
    r = sql_client.get("/public/users", params={"address__city": "Amsterdam"})
    assert {item["name"] for item in r.json()["items"]} == {"Ana", "Bram"}
    # Unlisted fields are simply not part of the surface.
    r = sql_client.get("/public/users", params={"email": "ana@example.com"})
    assert r.json()["total"] == 3
