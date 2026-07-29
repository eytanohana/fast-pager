"""The conformance runner's own semantics: battery shape + failure modes."""

import re

import pytest

from fast_pager import Capability
from fast_pager.ast import Group, PageSpec, Sort
from fast_pager.conformance import (
    CASES,
    UNCHECKED,
    ConformanceCase,
    is_supported,
    run_battery,
    run_case,
)
from fast_pager.errors import CompilationError

# --------------------------------------------------------------------------- #
# The battery itself                                                           #
# --------------------------------------------------------------------------- #


def test_case_ids_are_unique_and_stable_style():
    ids = [case.id for case in CASES]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z0-9-]+", case_id) for case_id in ids)


def test_battery_covers_every_registry_operator():
    from fast_pager.operators import DEFAULT_REGISTRY

    exercised = set().union(*(case.ops for case in CASES))
    assert set(DEFAULT_REGISTRY) <= exercised


def test_where_cases_derive_ops_and_requirements():
    by_id = {case.id: case for case in CASES}
    assert by_id["merge-same-field-range"].ops == {"gte", "lt"}
    assert by_id["nested-eq"].requires == {Capability.NESTED_PATHS}
    assert by_id["elem-relative-nested-path"].requires == {
        Capability.ELEM_MATCH,
        Capability.NESTED_PATHS,
    }
    assert by_id["elem-two-conditions-one-array"].requires == {Capability.ELEM_MATCH}
    assert by_id["group-or-nested-in-and"].ops == {"eq"}


# --------------------------------------------------------------------------- #
# Runner failure modes, via deliberately broken compilers                      #
# --------------------------------------------------------------------------- #


class _BaseCompiler:
    """A tiny stand-in adapter with tweakable behavior."""

    name = "dummy"
    supported_ops: frozenset[str] = frozenset({"eq", "gte", "lt", "gt"})
    capabilities: frozenset[Capability] = frozenset()

    def compile_where(self, group: Group) -> str:
        return "compiled"

    def compile_order(self, order: list[Sort]) -> str:
        return "ordered"

    def compile_page(self, page: PageSpec) -> str:
        return "paged"


def _case(case_id: str) -> ConformanceCase:
    return next(case for case in CASES if case.id == case_id)


def test_supported_case_with_matching_expected_passes():
    run_case(_BaseCompiler(), _case("scalar-eq"), expected="compiled")


def test_supported_case_with_wrong_expected_fails():
    with pytest.raises(AssertionError, match="scalar-eq.*does not match"):
        run_case(_BaseCompiler(), _case("scalar-eq"), expected="something else")


def test_custom_compare_is_used():
    run_case(
        _BaseCompiler(),
        _case("scalar-eq"),
        expected="COMPILED",
        compare=lambda got, want: got.upper() == want,
    )


def test_supported_case_that_raises_fails():
    class Broken(_BaseCompiler):
        def compile_where(self, group: Group) -> str:
            raise CompilationError("boom")

    with pytest.raises(AssertionError, match="declares support.*but failed to compile"):
        run_case(Broken(), _case("scalar-eq"))


def test_unsupported_case_must_raise_compilation_error():
    class Silent(_BaseCompiler):
        supported_ops = frozenset()

    with pytest.raises(AssertionError, match="never silently drop"):
        run_case(Silent(), _case("scalar-eq"))


def test_unsupported_case_error_must_name_the_operator():
    class Vague(_BaseCompiler):
        supported_ops = frozenset()

        def compile_where(self, group: Group) -> str:
            raise CompilationError("nope")

    with pytest.raises(AssertionError, match="must name the unsupported operator"):
        run_case(Vague(), _case("scalar-eq"))


def test_unsupported_case_with_named_operator_passes():
    class Loud(_BaseCompiler):
        supported_ops = frozenset()

        def compile_where(self, group: Group) -> str:
            raise CompilationError("dummy does not support operator 'eq'")

    run_case(Loud(), _case("scalar-eq"))


def test_missing_capability_rejection_passes_without_op_naming():
    class NoElem(_BaseCompiler):
        def compile_where(self, group: Group) -> str:
            raise CompilationError("no element matching here")

    # elem case: ops (eq/gte) are supported, the capability is not.
    run_case(NoElem(), _case("elem-two-conditions-one-array"))


def test_invalid_case_must_be_rejected():
    class Accepting(_BaseCompiler):
        supported_ops = frozenset({"has_key"})

    with pytest.raises(AssertionError, match="expected CompilationError"):
        run_case(Accepting(), _case("map-has-key-unsafe-dot"))


def test_invalid_case_rejection_passes():
    class Rejecting(_BaseCompiler):
        supported_ops = frozenset({"has_key"})

        def compile_where(self, group: Group) -> str:
            raise CompilationError("unsafe map key")

    run_case(Rejecting(), _case("map-has-key-unsafe-dot"))


def test_order_and_page_cases_check_expected():
    run_case(_BaseCompiler(), _case("order-two-keys"), expected="ordered")
    run_case(_BaseCompiler(), _case("page-window"), expected="paged")
    with pytest.raises(AssertionError, match="page-window"):
        run_case(_BaseCompiler(), _case("page-window"), expected="other")


def test_unchecked_expected_skips_output_comparison():
    run_case(_BaseCompiler(), _case("order-two-keys"), expected=UNCHECKED)


def test_run_battery_rejects_unknown_case_ids():
    with pytest.raises(AssertionError, match="unknown case ids.*no-such-case"):
        run_battery(_BaseCompiler(), {"no-such-case": 1})


def test_is_supported_intersects_ops_and_capabilities():
    compiler = _BaseCompiler()
    assert is_supported(compiler, _case("scalar-eq"))
    assert not is_supported(compiler, _case("scalar-in"))
    assert not is_supported(compiler, _case("elem-two-conditions-one-array"))


def test_unchecked_repr():
    assert repr(UNCHECKED) == "UNCHECKED"
