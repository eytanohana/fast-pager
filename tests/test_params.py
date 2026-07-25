"""Tests for parameter generation, config-time errors, and memoization."""

from typing import Annotated

import pytest
from pydantic import BaseModel, ValidationError

from conftest import Curated, User
from fast_pager import ConfigurationError, FilterConfig, Filterable
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
