"""`Filterable`: per-field filter metadata attached via ``Annotated``.

Design doc 01, Option A: declare filterability *where the field is declared*::

    class User(BaseModel):
        name: Annotated[str, Filterable(ops=["contains", "eq"])]
        age: Annotated[int, Filterable(ops=ops.ALL, source="ageYears")]
        bio: Annotated[str, Filterable(ops=ops.NONE)]  # explicitly unfilterable

The metadata is read once, at route registration, by
:func:`~fast_pager.introspection.introspect_model`; operator names are
validated against the field's type in :mod:`fast_pager.params`, so a bad
``Filterable`` raises :class:`~fast_pager.errors.ConfigurationError` before
the app serves traffic.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import ConfigurationError

__all__ = ["Filterable", "OpsMarker", "ops"]


class OpsMarker(enum.Enum):
    """Sentinel spellings for ``Filterable(ops=...)``: everything or nothing."""

    ALL = "all"
    """Every operator the field's type supports (still ``allow_regex``-gated)."""

    NONE = "none"
    """Explicitly unfilterable: the field generates no filter parameters."""


class _Ops:
    """The ``ops`` helper object (design doc 01): markers plus a bracket spelling.

    ``ops.ALL`` / ``ops.NONE`` are the two markers; ``ops["contains", "eq"]``
    is sugar for the plain tuple ``("contains", "eq")``.
    """

    ALL = OpsMarker.ALL
    NONE = OpsMarker.NONE

    def __getitem__(self, names: str | tuple[str, ...]) -> tuple[str, ...]:
        """Return a plain operator-name tuple: ``ops["contains", "eq"]``."""
        return (names,) if isinstance(names, str) else tuple(names)


ops = _Ops()
"""Operator-list helper: ``ops.ALL``, ``ops.NONE``, ``ops["contains", "eq"]``."""


@dataclass(frozen=True)
class Filterable:
    """Per-field filter metadata, attached as ``Annotated[T, Filterable(...)]``.

    Every attribute defaults to "no opinion" (``None``), so a bare
    ``Filterable()`` changes nothing — set only what you want to control.

    Attributes:
        ops: Exact operator allow-list for the field (e.g. ``["eq", "gte"]``),
            or ``ops.ALL`` (everything the type supports, still subject to the
            ``allow_regex`` gate), or ``ops.NONE`` (explicitly unfilterable —
            final: route-level config cannot re-enable the field). An explicit
            list beats ``FilterConfig.type_profiles``, which beats the global
            ``default_profile``; listing ``"regex"`` explicitly bypasses the
            ``allow_regex`` gate (the eyes-open opt-in). ``None`` defers to
            the coarser layers.
        source: Backend field name the compiled query uses (e.g. the Mongo
            document key) when it differs from the model. Beats the Pydantic
            alias, which beats the field name.
        param: Public query-parameter base name, decoupling the URL from the
            field (``?minimum_age=`` filtering the ``age`` field). Beats the
            Pydantic alias, which beats the field name. ``exclude``,
            ``operators`` and ``sortable`` entries in ``FilterConfig`` refer
            to this public name.
        sortable: Per-field sortable override. ``True`` makes the field
            sortable even when it is not filterable; ``False`` is final —
            naming the field in ``FilterConfig.sortable`` raises
            :class:`~fast_pager.errors.ConfigurationError`. ``None`` defers
            to the default (sortable iff filterable) or the config allow-list.
        keys: For ``dict[str, T]`` fields only: the enumerated map keys that
            get typed value-at-key parameters (``keys=["region"]`` generates
            ``metadata__region``, typed ``T``, compiling to
            ``{"metadata.region": value}``). Keys must be non-empty and free
            of ``.``, ``$``, and null bytes — they become segments of a
            backend field path. Setting ``keys`` on a non-map field raises
            :class:`~fast_pager.errors.ConfigurationError` at registration.
    """

    ops: Sequence[str] | OpsMarker | None = None
    source: str | None = None
    param: str | None = None
    sortable: bool | None = None
    keys: Sequence[str] | None = None

    def __post_init__(self) -> None:
        """Validate the shape of the metadata eagerly, at construction."""
        if isinstance(self.ops, str):
            # A bare string is almost certainly a mistake — "eq" would
            # iterate as 'e', 'q'. Require a sequence of names or a marker.
            raise ConfigurationError(
                "Filterable(ops=...) takes a sequence of operator names "
                f"(or ops.ALL / ops.NONE), got the bare string {self.ops!r}"
            )
        if self.source is not None and not self.source:
            raise ConfigurationError("Filterable(source=...) must be a non-empty string")
        if self.param is not None and not self.param:
            raise ConfigurationError("Filterable(param=...) must be a non-empty string")
        if self.keys is not None:
            self._validate_keys(self.keys)

    @staticmethod
    def _validate_keys(keys: Sequence[str] | str) -> None:
        if isinstance(keys, str):
            # Same trap as ops: a bare string would iterate as characters.
            raise ConfigurationError(
                f"Filterable(keys=...) takes a sequence of key names, got the bare string {keys!r}"
            )
        seen: set[str] = set()
        for key in keys:
            # Keys become segments of a backend field path (`metadata.region`),
            # so path metacharacters are configuration errors.
            if not isinstance(key, str) or not key or "." in key or "$" in key or "\x00" in key:
                raise ConfigurationError(
                    f"invalid map key {key!r} in Filterable(keys=...): keys must be "
                    "non-empty and must not contain '.', '$', or null bytes"
                )
            if key in seen:
                raise ConfigurationError(f"duplicate map key {key!r} in Filterable(keys=...)")
            seen.add(key)
