"""Tests for the Filterable metadata dataclass and the ops helper."""

import pytest

from fast_pager import ConfigurationError, Filterable, OpsMarker, ops


def test_ops_markers_are_the_enum_members():
    assert ops.ALL is OpsMarker.ALL
    assert ops.NONE is OpsMarker.NONE


def test_ops_bracket_spelling_returns_plain_tuples():
    assert ops["contains", "eq"] == ("contains", "eq")
    assert ops["eq"] == ("eq",)


def test_defaults_have_no_opinion():
    f = Filterable()
    assert f.ops is None
    assert f.source is None
    assert f.param is None
    assert f.sortable is None


def test_bare_string_ops_rejected():
    with pytest.raises(ConfigurationError, match="bare string"):
        Filterable(ops="eq")


@pytest.mark.parametrize("kwargs", [{"source": ""}, {"param": ""}])
def test_empty_names_rejected(kwargs):
    with pytest.raises(ConfigurationError, match="non-empty"):
        Filterable(**kwargs)


def test_keys_accepts_a_sequence_of_safe_names():
    assert Filterable(keys=["region", "tier"]).keys == ["region", "tier"]
    assert Filterable().keys is None


def test_bare_string_keys_rejected():
    with pytest.raises(ConfigurationError, match="bare string"):
        Filterable(keys="region")


@pytest.mark.parametrize("key", ["", "a.b", "a$b", "$region", "a\x00b", 7])
def test_unsafe_map_keys_rejected(key):
    with pytest.raises(ConfigurationError, match="invalid map key"):
        Filterable(keys=[key])


def test_duplicate_map_keys_rejected():
    with pytest.raises(ConfigurationError, match="duplicate map key 'region'"):
        Filterable(keys=["region", "tier", "region"])
