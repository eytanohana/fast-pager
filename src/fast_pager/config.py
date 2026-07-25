"""User-facing configuration for filter/sort/pagination generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .errors import ConfigurationError
from .operators import DEFAULT_REGISTRY, all_operators_for, type_name

__all__ = ["FilterConfig"]


@dataclass(frozen=True)
class FilterConfig:
    """Configuration knobs for one generated filter surface.

    All defaults are conservative (design doc 02 *Safety*): the ``safe``
    operator profile, ``regex`` disabled, bounded list/filter counts, and a
    hard ``max_limit`` on pagination.

    Attributes:
        default_profile: Operator tier exposed by default for every field.
        allow_regex: Gate for the ``regex`` operator. Even under the ``full``
            profile, ``regex`` params are only generated when this is true
            (or when ``regex`` is listed explicitly in ``operators``).
        operators: Optional per-field operator allow-list, keyed by the
            field's public (query-parameter) name. The finest layer: it beats
            field-level ``Filterable(ops=...)``, per-type ``type_profiles``,
            and the global profile — except ``Filterable(ops=ops.NONE)``,
            which is final. Unknown fields or operators invalid for the
            field's type raise :class:`~fast_pager.errors.ConfigurationError`
            at registration.
        type_profiles: Optional per-type operator allow-lists (e.g.
            ``{str: ["eq", "contains", "icontains"]}``), keyed by the
            resolved (Optional-unwrapped) field type; subclasses match via
            the MRO, most specific key first. Sits between the global
            ``default_profile`` and field-level ``Filterable(ops=...)``.
            Operators unknown or invalid for the keyed type raise
            :class:`~fast_pager.errors.ConfigurationError`; nullable-only
            operators (``isnull``/``exists``) in a profile are simply not
            emitted for non-nullable fields.
        unknown_params: What to do with a request parameter that looks like a
            filter (contains ``separator``) but matches no generated
            parameter: ``"ignore"`` (default) drops it silently; ``"strict"``
            returns a standard 422 naming the parameter. Parameters *without*
            the separator are never rejected — they may belong to the route.
        exclude: Public field names to leave out of the filter surface.
        sortable: Allow-list of sortable public field names. ``None`` means
            "same as the filterable fields", adjusted by any per-field
            ``Filterable(sortable=...)`` overrides.
        separator: Token between field name and operator in parameter names.
        default_limit: ``limit`` value when the client does not send one.
        max_limit: Upper bound on ``limit``; larger values are a 422.
        max_list_length: Cap on elements in list-valued params (``in``/...).
        max_filters: Cap on simultaneously applied filters per request.
    """

    default_profile: Literal["safe", "full"] = "safe"
    allow_regex: bool = False
    operators: Mapping[str, Sequence[str]] | None = None
    type_profiles: Mapping[type[Any], Sequence[str]] | None = None
    unknown_params: Literal["ignore", "strict"] = "ignore"
    exclude: Sequence[str] = ()
    sortable: Sequence[str] | None = None
    separator: str = "__"
    default_limit: int = 50
    max_limit: int = 100
    max_list_length: int = 100
    max_filters: int = 50

    def __post_init__(self) -> None:
        """Validate internally-consistent settings eagerly, at construction."""
        if self.default_profile not in ("safe", "full"):
            raise ConfigurationError(
                f"default_profile must be 'safe' or 'full', got {self.default_profile!r}"
            )
        if not self.separator:
            raise ConfigurationError("separator must be a non-empty string")
        for name in ("default_limit", "max_limit", "max_list_length", "max_filters"):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be >= 1, got {getattr(self, name)!r}")
        if self.default_limit > self.max_limit:
            raise ConfigurationError(
                f"default_limit ({self.default_limit}) must not exceed max_limit ({self.max_limit})"
            )
        if self.unknown_params not in ("ignore", "strict"):
            raise ConfigurationError(
                f"unknown_params must be 'ignore' or 'strict', got {self.unknown_params!r}"
            )
        self._validate_type_profiles()

    def _validate_type_profiles(self) -> None:
        """Reject type profiles keyed by unfilterable types or naming bad operators."""
        for py_type, names in (self.type_profiles or {}).items():
            tname = type_name(py_type)
            # nullable=True is the most permissive check: isnull/exists are
            # allowed here and simply not emitted for non-nullable fields.
            valid = all_operators_for(py_type, nullable=True)
            if not valid:
                raise ConfigurationError(
                    f"type {tname} in FilterConfig.type_profiles is not a filterable scalar type"
                )
            for op_name in names:
                if op_name not in DEFAULT_REGISTRY:
                    raise ConfigurationError(
                        f"unknown operator {op_name!r} for type {tname} in "
                        f"FilterConfig.type_profiles; known operators: "
                        f"{', '.join(sorted(DEFAULT_REGISTRY))}"
                    )
                if op_name not in valid:
                    raise ConfigurationError(
                        f"operator {op_name!r} is not valid for type {tname} in "
                        f"FilterConfig.type_profiles; valid operators for {tname}: "
                        f"{', '.join(valid)}"
                    )

    def _canonical(self) -> tuple[object, ...]:
        ops = tuple(sorted((k, tuple(v)) for k, v in (self.operators or {}).items()))
        profiles = tuple(
            sorted(
                ((k, tuple(v)) for k, v in (self.type_profiles or {}).items()),
                key=lambda item: (item[0].__module__, item[0].__qualname__),
            )
        )
        return (
            self.default_profile,
            self.allow_regex,
            ops,
            profiles,
            self.unknown_params,
            tuple(self.exclude),
            None if self.sortable is None else tuple(self.sortable),
            self.separator,
            self.default_limit,
            self.max_limit,
            self.max_list_length,
            self.max_filters,
        )

    def __hash__(self) -> int:
        # Fields may hold unhashable mappings/sequences; hash a canonical
        # tuple form so configs can key the per-(model, config) plan cache.
        return hash(self._canonical())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FilterConfig):
            return NotImplemented
        return self._canonical() == other._canonical()
