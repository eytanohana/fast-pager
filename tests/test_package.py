import fast_pager


def test_version_is_exposed():
    assert isinstance(fast_pager.__version__, str)
    assert fast_pager.__version__.count(".") == 2
