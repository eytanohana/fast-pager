"""Tests for `FilterSet`: allow-list surfaces, custom filters, FilterDepends wiring."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import Curated, Customer, Profile, Shopper, User
from fast_pager import (
    ConfigurationError,
    Filter,
    FilterConfig,
    FilterDepends,
    FilterQuery,
    FilterSet,
    ops,
)

# ---------------------------------------------------------------------------
# The two coexisting filter surfaces over the same model (doc 01 Option B).
# ---------------------------------------------------------------------------


class PublicUserFilter(FilterSet):
    class Meta:
        model = User
        fields = {
            "name": ["contains", "startswith"],
            "age": ["gte", "lte"],
        }


class AdminUserFilter(FilterSet):
    class Meta:
        model = User
        fields = {
            "name": "__all__",
            "age": "__all__",
            "status": ["eq", "in"],
            "nickname": ["eq", "isnull"],
        }
        config = FilterConfig(unknown_params="strict")
        sortable = ["age", "created_at"]

    created_since = Filter(
        field="created_at", op="gte", description="Created on or after this instant."
    )


def make_app(target, config=None) -> TestClient:
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


# ---------------------------------------------------------------------------
# Definition-time validation: a bad FilterSet fails at import, with the
# usual rich ConfigurationError.
# ---------------------------------------------------------------------------


def test_unknown_field_in_fields_mapping_raises_at_definition():
    with pytest.raises(ConfigurationError, match=r"unknown field 'nmae'.*known fields"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"nmae": ["eq"]}


def test_invalid_operator_for_type_raises_at_definition():
    with pytest.raises(ConfigurationError, match=r"'contains' is not valid for field 'age'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"age": ["contains"]}


def test_unknown_operator_names_the_known_set():
    with pytest.raises(ConfigurationError, match=r"unknown operator 'containz'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"name": ["containz"]}


def test_missing_model_raises():
    with pytest.raises(ConfigurationError, match=r"requires `Meta.model`"):

        class Bad(FilterSet):
            class Meta:
                fields = {"name": ["eq"]}


def test_non_pydantic_model_raises():
    with pytest.raises(ConfigurationError, match=r"requires `Meta.model`"):

        class Bad(FilterSet):
            class Meta:
                model = int


def test_fields_must_be_a_dict():
    with pytest.raises(ConfigurationError, match=r"`Meta.fields`.*must be a dict"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = ["name"]


def test_config_must_be_a_filter_config():
    with pytest.raises(ConfigurationError, match=r"`Meta.config`.*must be a FilterConfig"):

        class Bad(FilterSet):
            class Meta:
                model = User
                config = {"max_limit": 10}


def test_unknown_meta_attribute_raises():
    with pytest.raises(ConfigurationError, match=r"unknown Meta attribute\(s\) 'filds'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                filds = {"name": ["eq"]}


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("operators", {"name": ["eq"]}),
        ("exclude", ["name"]),
        ("sortable", ["age"]),
    ],
)
def test_replaced_config_knobs_are_rejected_in_meta_config(knob, value):
    with pytest.raises(ConfigurationError, match=rf"FilterConfig.{knob} is not allowed"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"name": ["eq"]}
                config = FilterConfig(**{knob: value})


def test_bare_string_ops_value_raises():
    with pytest.raises(ConfigurationError, match=r"bare string 'contains'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"name": "contains"}


def test_bare_string_sortable_raises():
    with pytest.raises(ConfigurationError, match=r"bare string 'age'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"age": ["gte"]}
                sortable = "age"


def test_ops_none_as_fields_value_says_omit_instead():
    with pytest.raises(ConfigurationError, match=r"omit the field"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"name": ops.NONE}


def test_all_on_a_field_with_no_operators_raises():
    with pytest.raises(ConfigurationError, match=r"supports no operators"):

        class Bad(FilterSet):
            class Meta:
                model = Customer
                # a non-nullable embedding has no operators of its own
                fields = {"address": "__all__"}


def test_text_search_inside_elements_raises():
    with pytest.raises(ConfigurationError, match=r"collection-level"):

        class Bad(FilterSet):
            class Meta:
                model = Shopper
                fields = {"orders__elem__ref": ["text_search"]}


def test_filterset_cannot_be_instantiated():
    with pytest.raises(TypeError, match=r"declarative FilterSet"):
        PublicUserFilter()


# ---------------------------------------------------------------------------
# Layering: the fields mapping is layer 4, but Filterable absolutes win.
# ---------------------------------------------------------------------------


def test_fields_mapping_overrides_filterable_ops_list():
    # Curated.name is Filterable(ops=["contains", "eq"]); the FilterSet
    # mapping (layer 4) decides otherwise.
    class NameFilter(FilterSet):
        class Meta:
            model = Curated
            fields = {"name": ["startswith"]}

    client = make_app(NameFilter)
    assert client.get("/items", params={"name__startswith": "al"}).status_code == 200
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert "name__startswith" in params
    assert "name__contains" not in params and "name" not in params


def test_listing_an_ops_none_field_raises():
    with pytest.raises(ConfigurationError, match=r"'ssn'.*ops.NONE"):

        class Bad(FilterSet):
            class Meta:
                model = Curated
                fields = {"ssn": ["eq"]}


def test_sortable_false_is_final_against_meta_sortable():
    with pytest.raises(ConfigurationError, match=r"'email'.*Filterable\(sortable=False\)"):

        class Bad(FilterSet):
            class Meta:
                model = Curated
                fields = {"email": ["eq"]}
                sortable = ["email"]


def test_all_respects_the_regex_gate_and_explicit_regex_bypasses_it():
    class Gated(FilterSet):
        class Meta:
            model = User
            fields = {"name": "__all__"}

    class Opted(FilterSet):
        class Meta:
            model = User
            fields = {"name": ["regex"], "nickname": ops.ALL}
            config = FilterConfig(allow_regex=True)

    gated = {p.url_name for p in Gated._fs_plan.params}
    opted = {p.url_name for p in Opted._fs_plan.params}
    assert "name__icontains" in gated and "name__regex" not in gated
    assert "name__regex" in opted and "nickname__regex" in opted


# ---------------------------------------------------------------------------
# Allow-list enforcement and end-to-end behavior.
# ---------------------------------------------------------------------------


def test_unlisted_fields_generate_nothing():
    client = make_app(PublicUserFilter)
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/items"]["get"]["parameters"]}
    assert params == {"name__contains", "name__startswith", "age__gte", "age__lte"} | {
        "limit",
        "offset",
        "sort",
    }
    # unlisted fields are silently ignored (default unknown_params="ignore")
    r = client.get("/items", params={"status__in": "active", "age__gte": "21"})
    assert r.status_code == 200
    assert r.json()["mongo"] == {"age": {"$gte": 21}}


def test_listed_operators_outside_the_list_do_not_exist():
    client = make_app(PublicUserFilter)
    r = client.get("/items", params={"age__eq": "21", "age": "21"})
    assert r.status_code == 200 and r.json()["applied"] == []


def test_bare_eq_sugar_applies_to_listed_eq():
    class F(FilterSet):
        class Meta:
            model = User
            fields = {"name": ["eq"]}

    client = make_app(F)
    assert client.get("/items", params={"name": "alice"}).json()["mongo"] == {"name": "alice"}


def test_meta_config_strict_mode_rejects_unlisted_fields():
    client = make_app(AdminUserFilter)
    assert client.get("/items", params={"score__gte": "1"}).status_code == 422
    assert client.get("/items", params={"age__gte": "21"}).status_code == 200


def test_meta_config_limits_apply():
    class Small(FilterSet):
        class Meta:
            model = User
            fields = {"age": ["gte"]}
            config = FilterConfig(default_limit=5, max_limit=10)

    client = make_app(Small)
    assert client.get("/items").json()["limit"] == 5
    assert client.get("/items", params={"limit": 11}).status_code == 422


def test_bad_values_still_return_422():
    client = make_app(PublicUserFilter)
    assert client.get("/items", params={"age__gte": "banana"}).status_code == 422


def test_nested_elem_and_map_paths_compile_end_to_end():
    class CustomerFilter(FilterSet):
        class Meta:
            model = Customer
            fields = {"address__city": ["contains"], "address__geo__lat": ["gte"]}

    class ShopperFilter(FilterSet):
        class Meta:
            model = Shopper
            # listing an elem path is the explicit opt-in (no full profile needed)
            fields = {"orders__elem__amount": ["gte"], "orders": ["len__eq"]}

    class ProfileFilter(FilterSet):
        class Meta:
            model = Profile
            fields = {"metadata": ["has_key"], "metadata__region": ["eq"]}

    r = make_app(CustomerFilter).get(
        "/items", params={"address__city__contains": "ams", "address__geo__lat__gte": "1.5"}
    )
    assert r.json()["mongo"] == {
        "address.city": {"$regex": "ams"},
        "address.geo.lat": {"$gte": 1.5},
    }
    r = make_app(ShopperFilter).get(
        "/items", params={"orders__elem__amount__gte": "100", "orders__len__eq": "2"}
    )
    assert r.json()["mongo"] == {"orders": {"$size": 2, "$elemMatch": {"amount": {"$gte": 100.0}}}}
    r = make_app(ProfileFilter).get(
        "/items", params={"metadata__has_key": "region", "metadata__region": "emea"}
    )
    assert r.json()["mongo"] == {"metadata.region": {"$exists": True, "$eq": "emea"}}


def test_source_and_param_renames_carry_over():
    class CuratedFilter(FilterSet):
        class Meta:
            model = Curated
            # keys use the *public* spelling: `points` is Filterable(param=...)
            fields = {"age": ["gte"], "points": ["gte"]}

    client = make_app(CuratedFilter)
    r = client.get("/items", params={"age__gte": "21", "points__gte": "1.5", "sort": "-age"})
    assert r.json()["mongo"] == {"ageYears": {"$gte": 21}, "score": {"$gte": 1.5}}
    assert r.json()["sort"] == [["ageYears", -1]]


def test_all_stays_eq_only_for_map_values_and_drops_text_search_in_elements():
    class F(FilterSet):
        class Meta:
            model = Profile
            fields = {"metadata__region": "__all__"}

    class G(FilterSet):
        class Meta:
            model = Shopper
            fields = {"orders__elem__ref": "__all__"}
            config = FilterConfig(default_profile="full")

    assert {p.operator.name for p in F._fs_plan.params} == {"eq"}
    elem_ops = {p.operator.name for p in G._fs_plan.params}
    assert "icontains" in elem_ops and "text_search" not in elem_ops


# ---------------------------------------------------------------------------
# Sorting: default "sortable iff listed", Meta.sortable allow-list.
# ---------------------------------------------------------------------------


def test_default_sortable_is_the_listed_scalar_fields():
    assert PublicUserFilter._fs_plan.sortable == frozenset({"name", "age"})
    client = make_app(PublicUserFilter)
    assert client.get("/items", params={"sort": "-age,name"}).status_code == 200
    assert client.get("/items", params={"sort": "created_at"}).status_code == 422


def test_meta_sortable_allow_list_wins_and_may_name_unlisted_fields():
    # AdminUserFilter sorts by `created_at` even though it is not filterable.
    client = make_app(AdminUserFilter)
    assert client.get("/items", params={"sort": "-created_at"}).json()["sort"] == [
        ["created_at", -1]
    ]
    assert client.get("/items", params={"sort": "name"}).status_code == 422


def test_filterable_sortable_true_forces_a_sort_only_field():
    class F(FilterSet):
        class Meta:
            model = Curated
            fields = {"age": ["gte"]}

    # Curated.joined is ops.NONE + sortable=True: sort-only, even unlisted.
    assert F._fs_plan.sortable == frozenset({"age", "joined"})


# ---------------------------------------------------------------------------
# Custom declared filters.
# ---------------------------------------------------------------------------


def test_declared_filter_end_to_end():
    client = make_app(AdminUserFilter)
    r = client.get("/items", params={"created_since": "2024-01-01T00:00:00Z"})
    assert r.status_code == 200
    assert r.json()["applied"] == [["created_at", "gte"]]
    assert r.json()["mongo"] == {"created_at": {"$gte": "2024-01-01T00:00:00+00:00"}}


def test_declared_filter_value_is_typed_by_the_target_field():
    client = make_app(AdminUserFilter)
    assert client.get("/items", params={"created_since": "not-a-date"}).status_code == 422


def test_declared_filter_param_override():
    class F(FilterSet):
        class Meta:
            model = User

        internal_name = Filter(field="age", op="gte", param="min_age")

    client = make_app(F)
    r = client.get("/items", params={"min_age": "21"})
    assert r.json()["mongo"] == {"age": {"$gte": 21}}
    assert "internal_name" not in F._fs_plan.known_params


def test_declared_filter_openapi_description_and_type():
    client = make_app(AdminUserFilter)
    spec = client.get("/openapi.json").json()
    params = {p["name"]: p for p in spec["paths"]["/items"]["get"]["parameters"]}
    created = params["created_since"]
    assert created["description"] == "Created on or after this instant."
    assert created["schema"]["anyOf"][0]["format"] == "date-time"


def test_declared_filter_validation_errors():
    with pytest.raises(ConfigurationError, match=r"unknown field 'nmae' in Bad.oops"):

        class Bad(FilterSet):
            class Meta:
                model = User

            oops = Filter(field="nmae", op="eq")

    with pytest.raises(ConfigurationError, match=r"'contains' is not valid for field 'age'"):

        class Bad2(FilterSet):
            class Meta:
                model = User

            oops = Filter(field="age", op="contains")

    with pytest.raises(ConfigurationError, match=r"'ssn'.*ops.NONE"):

        class Bad3(FilterSet):
            class Meta:
                model = Curated

            oops = Filter(field="ssn", op="eq")


def test_declared_filter_collisions_are_registration_errors():
    with pytest.raises(ConfigurationError, match=r"collision on 'age__gte'"):

        class Bad(FilterSet):
            class Meta:
                model = User
                fields = {"age": ["gte"]}

            age__gte = Filter(field="age", op="gte")

    with pytest.raises(ConfigurationError, match=r"collision on 'limit'"):

        class Bad2(FilterSet):
            class Meta:
                model = User

            limit = Filter(field="age", op="lte")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"field": "", "op": "eq"},
        {"field": "age", "op": ""},
        {"field": "age", "op": "eq", "param": ""},
    ],
)
def test_filter_declaration_shape_errors(kwargs):
    with pytest.raises(ConfigurationError):
        Filter(**kwargs)


def test_declared_filters_are_inherited_and_overridable():
    class Base(FilterSet):  # abstract: no Meta
        recent = Filter(field="created_at", op="gte")

    class Concrete(Base):
        class Meta:
            model = User
            fields = {"name": ["eq"]}

    class Overridden(Base):
        class Meta:
            model = User

        recent = Filter(field="created_at", op="lte")

    class Removed(Base):
        class Meta:
            model = User

        recent = None

    assert "recent" in Concrete._fs_plan.known_params
    (param,) = (p for p in Overridden._fs_plan.params if p.url_name == "recent")
    assert param.operator.name == "lte"
    assert "recent" not in Removed._fs_plan.known_params


def test_abstract_filterset_cannot_back_a_route():
    class Base(FilterSet):
        recent = Filter(field="created_at", op="gte")

    with pytest.raises(ConfigurationError, match=r"Base has no Meta"):
        FilterDepends(Base)


def test_filterset_with_only_declared_filters():
    class F(FilterSet):
        class Meta:
            model = User

        min_age = Filter(field="age", op="gte")

    client = make_app(F)
    assert client.get("/items", params={"min_age": "3"}).json()["mongo"] == {"age": {"$gte": 3}}
    assert F._fs_plan.sortable == frozenset()


# ---------------------------------------------------------------------------
# FilterDepends wiring and the uniform q surface.
# ---------------------------------------------------------------------------


def test_filter_depends_rejects_config_alongside_a_filterset():
    with pytest.raises(ConfigurationError, match=r"Meta.config"):
        FilterDepends(PublicUserFilter, config=FilterConfig())


def test_multiple_filtersets_per_model_coexist():
    app = FastAPI()

    @app.get("/public")
    def public(q: FilterQuery = FilterDepends(PublicUserFilter)):
        return {"mongo": q.to_mongo()}

    @app.get("/admin")
    def admin(q: FilterQuery = FilterDepends(AdminUserFilter)):
        return {"mongo": q.to_mongo()}

    client = TestClient(app)
    assert client.get("/public", params={"name__contains": "a"}).status_code == 200
    # admin-only params do not leak into the public surface (strict is
    # admin-only too, so /public just ignores them)…
    r = client.get("/public", params={"created_since": "2024-01-01T00:00:00Z"})
    assert r.status_code == 200 and r.json()["mongo"] == {}
    # …and the admin surface is strict about spellings outside its own
    # allow-list (`status` is listed with eq/in only).
    assert client.get("/admin", params={"status__contains": "act"}).status_code == 422
    assert client.get("/admin", params={"name__icontains": "A"}).status_code == 200


def test_filterset_query_surface_is_uniform_with_the_model_paths():
    zero_config = make_app(User)
    filterset = make_app(PublicUserFilter)
    params = {"age__gte": "21", "sort": "-age", "limit": 7, "offset": 3}
    assert (
        zero_config.get("/items", params=params).json()
        == filterset.get("/items", params=params).json()
    )
