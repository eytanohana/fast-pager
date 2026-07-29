"""`FilterDepends(..., backend=)`: the registration-time capability intersection."""

from typing import Any, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from conftest import Shopper, Tagged, User
from fast_pager import (
    Capability,
    FilterConfig,
    FilterDepends,
    FilterSet,
    MongoCompiler,
)
from fast_pager.backends.base import capabilities_for_path
from fast_pager.backends.sqlalchemy import SQLAlchemyCompiler
from fast_pager.errors import ConfigurationError


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "validation_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    age: Mapped[int]
    nickname: Mapped[Optional[str]]
    address: Mapped[dict[str, Any]] = mapped_column(sa.JSON)


sqlalchemy_backend = SQLAlchemyCompiler(UserRow)


# --------------------------------------------------------------------------- #
# capabilities_for_path                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("age", frozenset()),
        ("address.city", frozenset({Capability.NESTED_PATHS})),
        ("orders.$elem.amount", frozenset({Capability.ELEM_MATCH})),
        (
            "orders.$elem.supplier.name",
            frozenset({Capability.ELEM_MATCH, Capability.NESTED_PATHS}),
        ),
        (
            "orders.$elem.items.$elem.sku",
            frozenset({Capability.ELEM_MATCH}),
        ),
    ],
)
def test_capabilities_for_path(path, expected):
    assert capabilities_for_path(path) == expected


# --------------------------------------------------------------------------- #
# FilterDepends(backend=...)                                                   #
# --------------------------------------------------------------------------- #


def test_mongo_backend_accepts_every_generated_surface():
    FilterDepends(User, backend=MongoCompiler())
    FilterDepends(Tagged, backend=MongoCompiler())
    FilterDepends(Shopper, config=FilterConfig(default_profile="full"), backend=MongoCompiler())


def test_sqlalchemy_backend_accepts_a_compatible_surface():
    class NameAge(FilterSet):
        class Meta:
            model = User
            fields = {"name": ["contains", "startswith"], "age": ["gte", "lte"]}

    FilterDepends(NameAge, backend=sqlalchemy_backend)


def test_unsupported_operator_is_rejected_at_registration():
    # Tagged is all array fields: has/has_any/... are not SQL-expressible.
    with pytest.raises(ConfigurationError) as exc:
        FilterDepends(Tagged, backend=sqlalchemy_backend)
    message = str(exc.value)
    assert "'sqlalchemy' cannot serve the filter surface of Tagged" in message
    assert "'tags__has'" in message and "'has'" in message


def test_missing_capability_is_rejected_at_registration():
    with pytest.raises(ConfigurationError) as exc:
        FilterDepends(
            Shopper, config=FilterConfig(default_profile="full"), backend=sqlalchemy_backend
        )
    message = str(exc.value)
    assert "elem_match" in message
    assert "orders.$elem" in message


def test_filterset_surfaces_validate_against_the_backend():
    class ElemFilter(FilterSet):
        class Meta:
            model = Shopper
            fields = {"orders__elem__amount": ["gte"]}

    with pytest.raises(ConfigurationError, match="elem_match"):
        FilterDepends(ElemFilter, backend=sqlalchemy_backend)


def test_backend_none_skips_validation():
    # Without the hook the same surface registers fine (compile-time
    # rejection still applies when the query is actually compiled).
    FilterDepends(Tagged)


def test_core_never_imports_sqlalchemy():
    # The dependency stays behind the [sqlalchemy] extra: importing the core
    # package must not pull sqlalchemy in (checked in a fresh interpreter).
    import subprocess
    import sys as _sys

    code = (
        "import sys, fast_pager; "
        "assert not any(m == 'sqlalchemy' or m.startswith('sqlalchemy.') "
        "for m in sys.modules), 'core imported sqlalchemy'"
    )
    subprocess.run([_sys.executable, "-c", code], check=True)
