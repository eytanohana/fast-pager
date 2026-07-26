"""Tests for FilterConfig construction-time validation and hashing."""

import pytest

from fast_pager import ConfigurationError, FilterConfig


def test_defaults_are_safe():
    cfg = FilterConfig()
    assert cfg.default_profile == "safe"
    assert cfg.allow_regex is False
    assert cfg.default_limit == 50
    assert cfg.max_limit == 100
    assert cfg.max_list_length == 100
    assert cfg.separator == "__"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"default_profile": "loose"},
        {"separator": ""},
        {"default_limit": 0},
        {"max_limit": -1},
        {"max_list_length": 0},
        {"max_filters": 0},
        {"default_limit": 200, "max_limit": 100},
        {"max_depth": -1},
    ],
)
def test_invalid_config_raises(kwargs):
    with pytest.raises(ConfigurationError):
        FilterConfig(**kwargs)


def test_config_is_hashable_and_comparable_with_mappings():
    a = FilterConfig(operators={"name": ["eq", "contains"]}, exclude=["ssn"])
    b = FilterConfig(operators={"name": ("eq", "contains")}, exclude=("ssn",))
    assert a == b
    assert hash(a) == hash(b)
    assert a != FilterConfig()
    assert a.__eq__(object()) is NotImplemented


def test_type_profiles_accept_valid_operators():
    cfg = FilterConfig(type_profiles={str: ["eq", "icontains"], int: ["gte", "lte"]})
    assert cfg.type_profiles is not None


def test_type_profiles_unknown_operator_raises_with_known_list():
    with pytest.raises(ConfigurationError, match=r"'frobnicate'.*str.*known operators"):
        FilterConfig(type_profiles={str: ["frobnicate"]})


def test_type_profiles_operator_invalid_for_type_raises_with_valid_list():
    with pytest.raises(ConfigurationError, match=r"'gte'.*str.*valid operators for str"):
        FilterConfig(type_profiles={str: ["gte"]})


def test_type_profiles_unfilterable_type_raises():
    with pytest.raises(ConfigurationError, match="bytes"):
        FilterConfig(type_profiles={bytes: ["eq"]})


def test_unknown_params_mode_validated():
    assert FilterConfig(unknown_params="strict").unknown_params == "strict"
    with pytest.raises(ConfigurationError, match="unknown_params"):
        FilterConfig(unknown_params="loose")


def test_max_depth_default_and_zero():
    assert FilterConfig().max_depth == 2
    assert FilterConfig(max_depth=0).max_depth == 0  # nested traversal disabled
    assert FilterConfig(max_depth=2) == FilterConfig()
    assert FilterConfig(max_depth=3) != FilterConfig()


def test_config_hash_covers_type_profiles_and_unknown_params():
    a = FilterConfig(type_profiles={str: ["eq"]})
    b = FilterConfig(type_profiles={str: ("eq",)})
    assert a == b
    assert hash(a) == hash(b)
    assert a != FilterConfig()
    assert FilterConfig(unknown_params="strict") != FilterConfig()
