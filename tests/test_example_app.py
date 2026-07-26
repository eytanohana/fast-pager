"""Tests for `examples/mongo_app`: the Stage 3 example app stays runnable.

The example is a plain script directory (not a package), so it is imported
by putting its directory on ``sys.path`` — the same import a user gets from
``uvicorn main:app`` inside ``examples/mongo_app``.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "mongo_app"))

from main import app  # noqa: E402  (path setup must run first)

client = TestClient(app)


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
