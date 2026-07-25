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
