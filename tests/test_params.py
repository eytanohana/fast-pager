"""Tests for parameter generation, config-time errors, and memoization."""

from typing import Annotated, Optional

import pytest
from pydantic import BaseModel, ValidationError

from conftest import Address, Curated, Customer, Tagged, User
from fast_pager import ConfigurationError, FilterConfig, Filterable, ops
from fast_pager.params import build_plan


def url_names(plan):
    return {p.url_name for p in plan.params}


def test_safe_profile_generates_expected_str_params():
    plan = build_plan(User, FilterConfig())
    names = url_names(plan)
    assert {"name", "name__eq", "name__ne", "name__in", "name__nin"} <= names
    assert {"name__contains", "name__startswith", "name__endswith"} <= names
    assert "name__icontains" not in names  # full tier
    assert "name__regex" not in names  # full tier + gated


def test_bare_equality_param_emitted_per_field():
    plan = build_plan(User, FilterConfig())
    names = url_names(plan)
    for field in ("name", "age", "active", "created_at", "uid", "color", "status"):
        assert field in names and f"{field}__eq" in names


def test_nullable_field_gets_isnull():
    names = url_names(build_plan(User, FilterConfig()))
    assert "nickname__isnull" in names
    assert "name__isnull" not in names
    assert "nickname__exists" not in names  # full tier


def test_full_profile_adds_full_ops_but_regex_stays_gated():
    names = url_names(build_plan(User, FilterConfig(default_profile="full")))
    assert {"name__icontains", "age__between", "nickname__exists"} <= names
    assert "name__regex" not in names


def test_allow_regex_gate():
    cfg = FilterConfig(default_profile="full", allow_regex=True)
    assert "name__regex" in url_names(build_plan(User, cfg))


def test_custom_separator():
    names = url_names(build_plan(User, FilterConfig(separator="_")))
    assert "age_gte" in names and "age__gte" not in names


def test_exclude_removes_field():
    plan = build_plan(User, FilterConfig(exclude=["name"]))
    assert not any(n == "name" or n.startswith("name__") for n in url_names(plan))
    assert "name" not in plan.sortable


def test_exclude_unknown_field_raises_naming_field():
    with pytest.raises(ConfigurationError, match="'bogus'"):
        build_plan(User, FilterConfig(exclude=["bogus"]))


def test_per_field_operator_override():
    plan = build_plan(User, FilterConfig(operators={"name": ["contains"]}))
    names = url_names(plan)
    assert "name__contains" in names
    assert "name__eq" not in names and "name" not in names


def test_operator_invalid_for_type_raises_with_field_and_operator():
    with pytest.raises(ConfigurationError, match=r"'contains'.*'age'"):
        build_plan(User, FilterConfig(operators={"age": ["contains"]}))


def test_unknown_operator_raises():
    with pytest.raises(ConfigurationError, match=r"'frobnicate'.*'name'"):
        build_plan(User, FilterConfig(operators={"name": ["frobnicate"]}))


def test_operators_for_unknown_field_raises():
    with pytest.raises(ConfigurationError, match="'bogus'"):
        build_plan(User, FilterConfig(operators={"bogus": ["eq"]}))


def test_sortable_defaults_to_filterable_fields():
    plan = build_plan(User, FilterConfig())
    assert "age" in plan.sortable and "nickname" in plan.sortable


def test_sortable_allow_list_validated_at_registration():
    plan = build_plan(User, FilterConfig(sortable=["age"]))
    assert plan.sortable == frozenset({"age"})
    with pytest.raises(ConfigurationError, match="'password'"):
        build_plan(User, FilterConfig(sortable=["password"]))


def test_field_colliding_with_reserved_param_raises():
    class M(BaseModel):
        limit: int

    with pytest.raises(ConfigurationError, match="'limit'"):
        build_plan(M, FilterConfig())


def test_generated_name_collision_raises_naming_both_sources():
    class M(BaseModel):
        name: str
        name__eq: str

    with pytest.raises(ConfigurationError, match="name__eq"):
        build_plan(M, FilterConfig())


def test_plan_is_memoized_per_model_and_config():
    cfg = FilterConfig()
    assert build_plan(User, cfg) is build_plan(User, FilterConfig())
    assert build_plan(User, cfg) is not build_plan(User, FilterConfig(max_limit=99))


def test_params_model_validates_and_coerces():
    plan = build_plan(User, FilterConfig())
    parsed = plan.params_model.model_validate(
        {"age__gte": "21", "status__in": ["active,trial"], "age__in": ["1", "2,3"]}
    )
    assert parsed.f_age__gte == 21
    assert parsed.f_status__in == ["active", "trial"]
    assert parsed.f_age__in == [1, 2, 3]
    assert parsed.limit == 50 and parsed.offset == 0


def test_max_list_length_enforced():
    plan = build_plan(User, FilterConfig(max_list_length=2))
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"age__in": ["1,2,3"]})


def test_between_requires_exactly_two_values():
    plan = build_plan(User, FilterConfig(default_profile="full"))
    parsed = plan.params_model.model_validate({"age__between": ["21,65"]})
    assert parsed.f_age__between == [21, 65]
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"age__between": ["21"]})
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"age__between": ["1,2,3"]})


def test_max_filters_enforced():
    plan = build_plan(User, FilterConfig(max_filters=1))
    with pytest.raises(ValidationError, match="max_filters"):
        plan.params_model.model_validate({"age__gte": "1", "age__lt": "2"})


def test_sort_validator_rejects_unknown_and_empty_tokens():
    plan = build_plan(User, FilterConfig())
    assert plan.params_model.model_validate({"sort": "-age,name"}).sort == "-age,name"
    with pytest.raises(ValidationError, match="not sortable"):
        plan.params_model.model_validate({"sort": "bogus"})
    with pytest.raises(ValidationError, match="empty sort field"):
        plan.params_model.model_validate({"sort": "age,,name"})


def test_limit_bounds():
    plan = build_plan(User, FilterConfig())
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"limit": 101})
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"limit": 0})
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"offset": -1})


def test_split_commas_handles_bare_strings():
    from fast_pager.params import _split_commas

    assert _split_commas("a,b") == ["a", "b"]
    assert _split_commas("a") == "a"
    assert _split_commas(7) == 7


def test_sort_validator_passes_none_through():
    plan = build_plan(User, FilterConfig())
    assert plan.params_model.model_validate({"sort": None}).sort is None


# ---------------------------------------------------------------------------
# Stage 2: Filterable metadata, type_profiles, and the layering rules.
# ---------------------------------------------------------------------------


def names_for(plan, field):
    prefix = f"{field}__"
    return {n for n in url_names(plan) if n == field or n.startswith(prefix)}


def test_filterable_ops_list_is_exact():
    plan = build_plan(Curated, FilterConfig())
    assert names_for(plan, "name") == {"name", "name__eq", "name__contains"}


def test_filterable_ops_all_includes_full_tier_but_regex_stays_gated():
    names = url_names(build_plan(Curated, FilterConfig()))
    assert {"slug__icontains", "slug__text_search", "slug__iendswith"} <= names
    assert "slug__regex" not in names
    gated_open = url_names(build_plan(Curated, FilterConfig(allow_regex=True)))
    assert "slug__regex" in gated_open


def test_explicit_regex_in_filterable_ops_bypasses_the_gate():
    class M(BaseModel):
        name: Annotated[str, Filterable(ops=["regex"])]

    assert "name__regex" in url_names(build_plan(M, FilterConfig()))


def test_filterable_invalid_operator_error_names_field_op_and_valid_set():
    class M(BaseModel):
        age: Annotated[int, Filterable(ops=["contains"])]

    with pytest.raises(
        ConfigurationError,
        match=r"operator 'contains' is not valid for field 'age' of type int.*"
        r"valid operators for int: eq, ne, gt, gte",
    ):
        build_plan(M, FilterConfig())


def test_filterable_unknown_operator_error_names_known_operators():
    class M(BaseModel):
        age: Annotated[int, Filterable(ops=["frobnicate"])]

    with pytest.raises(ConfigurationError, match=r"'frobnicate'.*'age'.*known operators"):
        build_plan(M, FilterConfig())


def test_ops_none_removes_field_from_filter_surface_and_sortable():
    plan = build_plan(Curated, FilterConfig())
    assert names_for(plan, "ssn") == set()
    assert "ssn" not in plan.sortable


def test_ops_none_field_cannot_be_configured_in_operators():
    with pytest.raises(ConfigurationError, match=r"'ssn'.*ops\.NONE"):
        build_plan(Curated, FilterConfig(operators={"ssn": ["eq"]}))


def test_sortable_true_makes_ops_none_field_sort_only():
    plan = build_plan(Curated, FilterConfig())
    assert names_for(plan, "joined") == set()
    assert "joined" in plan.sortable
    assert plan.sources["joined"] == "joined"


def test_sortable_false_removes_field_from_default_sortable_set():
    plan = build_plan(Curated, FilterConfig())
    assert "email" in {p.spec.public_name for p in plan.params}  # still filterable
    assert "email" not in plan.sortable


def test_sortable_false_conflicts_with_config_sortable():
    with pytest.raises(ConfigurationError, match=r"'email'.*sortable=False"):
        build_plan(Curated, FilterConfig(sortable=["email"]))


def test_config_sortable_may_name_a_sort_only_field():
    plan = build_plan(Curated, FilterConfig(sortable=["joined"]))
    assert plan.sortable == frozenset({"joined"})


def test_source_mapped_for_compiled_queries():
    plan = build_plan(Curated, FilterConfig())
    assert plan.sources["age"] == "ageYears"


def test_param_renames_the_public_surface():
    plan = build_plan(Curated, FilterConfig())
    names = url_names(plan)
    assert "points__gte" in names and "points" in names
    assert names_for(plan, "score") == set()
    assert "points" in plan.sortable and "score" not in plan.sortable
    assert plan.sources["points"] == "score"


def test_config_entries_are_keyed_by_the_public_param_name():
    plan = build_plan(Curated, FilterConfig(operators={"points": ["gte"]}))
    assert names_for(plan, "points") == {"points__gte"}
    with pytest.raises(ConfigurationError, match="'score'"):
        build_plan(Curated, FilterConfig(operators={"score": ["gte"]}))


def test_type_profiles_override_the_default_profile():
    plan = build_plan(User, FilterConfig(type_profiles={str: ["eq", "icontains"]}))
    assert names_for(plan, "name") == {"name", "name__eq", "name__icontains"}
    assert "age__gte" in url_names(plan)  # other types untouched


def test_type_profiles_nullable_only_ops_dropped_for_non_nullable_fields():
    plan = build_plan(User, FilterConfig(type_profiles={str: ["eq", "isnull"]}))
    names = url_names(plan)
    assert "name__isnull" not in names
    assert "nickname__isnull" in names


def test_type_profiles_match_subclasses_via_mro():
    import enum

    plan = build_plan(User, FilterConfig(type_profiles={enum.Enum: ["eq"]}))
    assert names_for(plan, "color") == {"color", "color__eq"}


def test_type_profiles_exact_key_beats_base_class_key():
    plan = build_plan(User, FilterConfig(type_profiles={int: ["eq"], bool: ["eq", "ne"]}))
    assert names_for(plan, "active") == {"active", "active__eq", "active__ne"}
    assert names_for(plan, "age") == {"age", "age__eq"}


def test_filterable_ops_beat_type_profiles():
    plan = build_plan(Curated, FilterConfig(type_profiles={str: ["eq", "icontains"]}))
    assert names_for(plan, "name") == {"name", "name__eq", "name__contains"}


def test_config_operators_beat_filterable_ops():
    plan = build_plan(Curated, FilterConfig(operators={"name": ["startswith"]}))
    assert names_for(plan, "name") == {"name__startswith"}


def test_plan_records_known_params_for_strict_mode():
    plan = build_plan(User, FilterConfig())
    assert {"limit", "offset", "sort", "name", "age__gte"} <= plan.known_params
    assert "name__bogus" not in plan.known_params


# ---------------------------------------------------------------------------
# Phase 3a: arrays of scalars.
# ---------------------------------------------------------------------------

ARRAY_SAFE_PARAMS = {"tags__has", "tags__has_any", "tags__has_all", "tags__len__eq", "tags__empty"}


def test_array_safe_profile_generates_membership_and_shape_params():
    plan = build_plan(Tagged, FilterConfig())
    assert names_for(plan, "tags") == ARRAY_SAFE_PARAMS


def test_array_fields_get_no_scalar_operators_and_no_bare_equality():
    names = url_names(build_plan(Tagged, FilterConfig(default_profile="full")))
    assert "tags" not in names  # no bare-eq sugar for arrays
    for scalar_op in ("eq", "ne", "in", "nin", "contains", "startswith", "regex"):
        assert f"tags__{scalar_op}" not in names
    for scalar_op in ("eq", "gt", "gte", "lt", "lte", "between"):
        assert f"scores__{scalar_op}" not in names


def test_array_full_profile_adds_len_comparisons():
    safe = url_names(build_plan(Tagged, FilterConfig()))
    full = url_names(build_plan(Tagged, FilterConfig(default_profile="full")))
    ranges = {"tags__len__ne", "tags__len__gt", "tags__len__gte", "tags__len__lt", "tags__len__lte"}
    assert not ranges & safe
    assert ranges <= full


def test_optional_list_gets_isnull_and_gated_exists():
    safe = url_names(build_plan(Tagged, FilterConfig()))
    full = url_names(build_plan(Tagged, FilterConfig(default_profile="full")))
    assert "labels__isnull" in safe and "labels__exists" not in safe
    assert "labels__exists" in full
    assert "tags__isnull" not in safe  # non-nullable array


def test_array_params_coerce_to_the_element_type():
    plan = build_plan(Tagged, FilterConfig(default_profile="full"))
    parsed = plan.params_model.model_validate(
        {
            "scores__has": "3",
            "scores__has_any": ["1,2", "3"],
            "tags__len__gte": "2",
            "tags__empty": "true",
        }
    )
    assert parsed.f_scores__has == 3
    assert parsed.f_scores__has_any == [1, 2, 3]
    assert parsed.f_tags__len__gte == 2
    assert parsed.f_tags__empty is True
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"scores__has": "banana"})
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"tags__len__eq": "many"})


def test_max_list_length_applies_to_has_any_and_has_all():
    plan = build_plan(Tagged, FilterConfig(max_list_length=2))
    assert plan.params_model.model_validate({"tags__has_any": ["a,b"]}).f_tags__has_any == [
        "a",
        "b",
    ]
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"tags__has_any": ["a,b,c"]})
    with pytest.raises(ValidationError):
        plan.params_model.model_validate({"tags__has_all": ["a,b,c"]})


def test_filterable_ops_curation_on_an_array_field():
    class M(BaseModel):
        tags: Annotated[list[str], Filterable(ops=["has", "empty"])]

    plan = build_plan(M, FilterConfig())
    assert names_for(plan, "tags") == {"tags__has", "tags__empty"}


def test_config_operators_curation_on_an_array_field():
    plan = build_plan(Tagged, FilterConfig(operators={"tags": ["has_any"]}))
    assert names_for(plan, "tags") == {"tags__has_any"}


def test_scalar_operator_on_array_field_raises_naming_the_list_type():
    class M(BaseModel):
        tags: Annotated[list[str], Filterable(ops=["contains"])]

    with pytest.raises(
        ConfigurationError,
        match=r"operator 'contains' is not valid for field 'tags' of type list\[str\].*"
        r"valid operators for list\[str\]: has, has_any, has_all",
    ):
        build_plan(M, FilterConfig())


def test_type_profiles_do_not_apply_to_array_fields():
    plan = build_plan(Tagged, FilterConfig(type_profiles={str: ["eq", "icontains"]}))
    assert names_for(plan, "tags") == ARRAY_SAFE_PARAMS  # element profile ignored
    assert names_for(plan, "name") == {"name", "name__eq", "name__icontains"}


def test_array_fields_are_not_sortable_by_default():
    plan = build_plan(Tagged, FilterConfig())
    assert "name" in plan.sortable
    assert plan.sortable & {"tags", "scores", "colors", "codes", "labels"} == set()


def test_filterable_sortable_true_forces_an_array_field_sortable():
    class M(BaseModel):
        tags: Annotated[list[str], Filterable(sortable=True)]

    assert "tags" in build_plan(M, FilterConfig()).sortable


def test_config_sortable_allow_list_may_name_an_array_field():
    plan = build_plan(Tagged, FilterConfig(sortable=["tags"]))
    assert plan.sortable == frozenset({"tags"})


# ---------------------------------------------------------------------------
# Phase 3b: nested Pydantic models.
# ---------------------------------------------------------------------------


def test_nested_params_generated_with_exact_dotted_spellings():
    names = url_names(build_plan(Customer, FilterConfig()))
    assert {"address__city", "address__city__eq", "address__city__contains"} <= names
    assert {"address__geo__lat", "address__geo__lat__gte"} <= names
    assert "address__zip_code__startswith" in names


def test_nested_array_field_gets_the_array_operator_family():
    plan = build_plan(Customer, FilterConfig())
    assert names_for(plan, "address__tags") == {
        "address__tags__has",
        "address__tags__has_any",
        "address__tags__has_all",
        "address__tags__len__eq",
        "address__tags__empty",
    }


def test_nullable_embedding_gets_isnull_non_nullable_gets_no_params():
    safe = url_names(build_plan(Customer, FilterConfig()))
    full = url_names(build_plan(Customer, FilterConfig(default_profile="full")))
    assert "billing__isnull" in safe and "billing__exists" not in safe
    assert "billing__exists" in full
    assert "address__isnull" not in full  # non-nullable embedding
    assert "address" not in safe and "billing" not in safe  # no bare-eq sugar


def test_children_of_a_nullable_embedding_behave_normally():
    names = url_names(build_plan(Customer, FilterConfig()))
    assert {"billing__city__contains", "billing__geo__lat__gte"} <= names


def test_max_depth_config_bounds_the_parameter_surface():
    one = url_names(build_plan(Customer, FilterConfig(max_depth=1)))
    assert "address__city" in one
    assert "address__geo__lat" not in one
    zero = url_names(build_plan(Customer, FilterConfig(max_depth=0)))
    assert "billing__isnull" in zero  # the embedding itself is a root field
    assert "address__city" not in zero


def test_ops_none_on_embedding_removes_the_subtree_from_the_surface():
    class M(BaseModel):
        name: str
        address: Annotated[Address, Filterable(ops=ops.NONE)]

    plan = build_plan(M, FilterConfig())
    assert names_for(plan, "address") == set()
    assert not any(n.startswith("address__") for n in url_names(plan))
    assert "address__city" not in plan.sortable


def test_ops_none_embedding_cannot_be_configured_in_operators():
    class M(BaseModel):
        address: Annotated[Optional[Address], Filterable(ops=ops.NONE)] = None

    with pytest.raises(ConfigurationError, match=r"'address'.*ops\.NONE"):
        build_plan(M, FilterConfig(operators={"address": ["isnull"]}))


def test_operators_config_keyed_by_the_dotted_public_spelling():
    plan = build_plan(Customer, FilterConfig(operators={"address__city": ["contains"]}))
    assert names_for(plan, "address__city") == {"address__city__contains"}


def test_operators_on_a_non_nullable_embedding_raise_with_empty_valid_set():
    with pytest.raises(
        ConfigurationError,
        match=r"'isnull' is not valid for field 'address' of type nested model Address.*\(none\)",
    ):
        build_plan(Customer, FilterConfig(operators={"address": ["isnull"]}))


def test_operators_on_a_nullable_embedding_allow_nullability_ops():
    plan = build_plan(Customer, FilterConfig(operators={"billing": ["exists"]}))
    names = url_names(plan)
    assert "billing__exists" in names and "billing__isnull" not in names
    assert "billing__city" in names  # children are configured independently


def test_filterable_ops_list_on_embedding_field_is_validated():
    class M(BaseModel):
        address: Annotated[Address, Filterable(ops=["isnull"])]

    with pytest.raises(ConfigurationError, match=r"nested model Address.*\(none\)"):
        build_plan(M, FilterConfig())

    class M2(BaseModel):
        billing: Annotated[Optional[Address], Filterable(ops=["isnull"])] = None

    names = url_names(build_plan(M2, FilterConfig()))
    assert "billing__isnull" in names
    assert "billing__city" in names  # ops on the embedding never touch children


def test_exclude_a_nested_leaf_and_a_whole_subtree():
    plan = build_plan(Customer, FilterConfig(exclude=["address__city", "billing"]))
    names = url_names(plan)
    assert "address__city" not in names and "address__city__eq" not in names
    assert "address__zip_code" in names  # siblings untouched
    assert not any(n == "billing" or n.startswith("billing__") for n in names)
    assert "billing__city" not in plan.sortable


def test_exclude_matches_path_prefixes_not_string_prefixes():
    class M(BaseModel):
        address: Address
        address__city: str  # a literal field name containing the separator

    plan = build_plan(M, FilterConfig(exclude=["address"]))
    names = url_names(plan)
    assert "address__city" in names  # the literal field survives
    assert "address__zip_code" not in names  # the nested subtree is gone


def test_nested_name_collision_with_literal_field_names_both_sources():
    class M(BaseModel):
        address: Address
        address__city: str

    with pytest.raises(
        ConfigurationError,
        match=r"collision on 'address__city__eq'.*source 'address__city'.*source 'address\.city'",
    ):
        build_plan(M, FilterConfig())


def test_type_profiles_apply_to_nested_scalar_leaves():
    plan = build_plan(Customer, FilterConfig(type_profiles={str: ["eq", "icontains"]}))
    assert names_for(plan, "address__city") == {
        "address__city",
        "address__city__eq",
        "address__city__icontains",
    }
    # Element-type profiles still never apply to array fields, nested or not.
    assert "address__tags__icontains" not in url_names(plan)
    assert "address__tags__has" in url_names(plan)


def test_nested_leaves_sortable_by_default_embeddings_and_arrays_not():
    plan = build_plan(Customer, FilterConfig())
    assert {"address__city", "address__geo__lat", "billing__city"} <= plan.sortable
    assert "address" not in plan.sortable
    assert "address__tags" not in plan.sortable


def test_config_sortable_may_name_nested_fields():
    plan = build_plan(Customer, FilterConfig(sortable=["address__city"]))
    assert plan.sortable == frozenset({"address__city"})


def test_filterable_sortable_false_on_a_nested_leaf_is_final():
    class Inner(BaseModel):
        secret: Annotated[str, Filterable(sortable=False)]

    class M(BaseModel):
        inner: Inner

    assert "inner__secret" not in build_plan(M, FilterConfig()).sortable
    with pytest.raises(ConfigurationError, match=r"'inner__secret'.*sortable=False"):
        build_plan(M, FilterConfig(sortable=["inner__secret"]))


def test_sources_map_nested_public_names_to_dotted_sources():
    plan = build_plan(Customer, FilterConfig())
    assert plan.sources["address__city"] == "address.city"
    assert plan.sources["address__zip_code"] == "address.zip"
    assert plan.sources["address__geo__lat"] == "address.geo.lat"


def test_custom_separator_applies_to_nested_paths():
    names = url_names(build_plan(Customer, FilterConfig(separator="_")))
    assert "address_city_contains" in names and "address__city__contains" not in names
