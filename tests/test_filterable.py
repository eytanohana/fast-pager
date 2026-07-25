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
