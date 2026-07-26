"""Parameter generation: FieldSpec × operators → a dynamic Pydantic query model.

The whole parameter surface is generated at registration time. Each generated
parameter carries its own ``(field, operator)`` pair and an incoming name is
matched *exactly* against the generated set — there is no request-time ``__``
splitting (design doc 02, *Parameter matching*).

The generated model is used natively by FastAPI ≥ 0.115 as a query-parameter
group (``Annotated[Model, Query()]``); the spike result is recorded in
``docs/design/03-architecture.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Optional

from annotated_types import Len
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    create_model,
    model_validator,
)

from .config import FilterConfig
from .errors import ConfigurationError
from .filterable import OpsMarker
from .introspection import FieldSpec, introspect_model, public_field_names
from .operators import (
    DEFAULT_REGISTRY,
    Arity,
    Container,
    Operator,
    ValueTypeRule,
    all_operators_for,
    operators_for,
    type_name,
)

__all__ = ["QueryPlan", "ResolvedParam", "build_plan"]

#: Parameter names reserved for pagination and sorting.
RESERVED_PARAMS: tuple[str, ...] = ("limit", "offset", "sort")

_OP_HELP: dict[str, str] = {
    "eq": "equals",
    "ne": "does not equal",
    "gt": "is greater than",
    "gte": "is greater than or equal to",
    "lt": "is less than",
    "lte": "is less than or equal to",
    "in": "is any of (repeat the key or comma-join values)",
    "nin": "is none of (repeat the key or comma-join values)",
    "between": "is between the two comma-joined bounds (inclusive)",
    "contains": "contains the literal substring",
    "icontains": "contains the literal substring (case-insensitive)",
    "startswith": "starts with the literal prefix",
    "istartswith": "starts with the literal prefix (case-insensitive)",
    "endswith": "ends with the literal suffix",
    "iendswith": "ends with the literal suffix (case-insensitive)",
    "regex": "matches the regular expression (config-gated)",
    "text_search": "matches via the backend text index",
    "isnull": "is null (true) or is not null (false)",
    "exists": "exists in the document (true) or is missing (false)",
    "has": "array contains the element",
    "has_any": "array contains any of these elements (repeat the key or comma-join values)",
    "has_all": "array contains all of these elements (repeat the key or comma-join values)",
    "len__eq": "array length equals",
    "len__ne": "array length does not equal",
    "len__gt": "array length is greater than",
    "len__gte": "array length is greater than or equal to",
    "len__lt": "array length is less than",
    "len__lte": "array length is less than or equal to",
    "empty": "array is empty (true) or non-empty (false); a missing field matches neither",
}


@dataclass(frozen=True)
class ResolvedParam:
    """One generated query parameter bound to a ``(field, operator)`` pair."""

    url_name: str
    python_name: str
    spec: FieldSpec
    operator: Operator


@dataclass(frozen=True, eq=False)
class QueryPlan:
    """Everything derived from one ``(model, config)`` pair at registration.

    Built once and memoized; request handling only reads it.
    """

    model: type[BaseModel]
    config: FilterConfig
    params: tuple[ResolvedParam, ...]
    params_model: type[BaseModel]
    sortable: frozenset[str]
    sources: dict[str, str]
    known_params: frozenset[str]


_PLAN_CACHE: dict[tuple[type[BaseModel], FilterConfig], QueryPlan] = {}


def _split_commas(value: object) -> object:
    """BeforeValidator: expand comma-joined items inside a list of raw strings."""
    if isinstance(value, list):
        out: list[object] = []
        for item in value:
            if isinstance(item, str) and "," in item:
                out.extend(part for part in item.split(","))
            else:
                out.append(item)
        return out
    if isinstance(value, str) and "," in value:
        return value.split(",")
    return value


def _python_name(url_name: str) -> str:
    """Sanitize a public parameter name into a valid model attribute name."""
    return "f_" + re.sub(r"\W", "_", url_name)


def _is_unfilterable(spec: FieldSpec) -> bool:
    """Whether the field is explicitly opted out via ``Filterable(ops=ops.NONE)``."""
    return spec.filterable is not None and spec.filterable.ops is OpsMarker.NONE


def _validated_ops(names: tuple[str, ...], spec: FieldSpec, where: str) -> tuple[str, ...]:
    """Validate an explicit operator list for one field, with rich errors."""
    valid = all_operators_for(spec.py_type, spec.nullable, spec.container)
    tname = type_name(spec.py_type)
    if spec.container is Container.LIST:
        tname = f"list[{tname}]"
    for op_name in names:
        if op_name not in DEFAULT_REGISTRY:
            raise ConfigurationError(
                f"unknown operator {op_name!r} for field {spec.public_name!r} in {where}; "
                f"known operators: {', '.join(sorted(DEFAULT_REGISTRY))}"
            )
        if op_name not in valid:
            raise ConfigurationError(
                f"operator {op_name!r} is not valid for field {spec.public_name!r} of "
                f"type {tname} in {where}; valid operators for {tname}: {', '.join(valid)}"
            )
    return names


def _match_type_profile(py_type: Any, config: FilterConfig) -> tuple[str, ...] | None:
    """Find the ``type_profiles`` entry for a resolved field type, if any.

    An exact key match wins; otherwise base classes match in MRO order (most
    specific first), so ``{enum.Enum: [...]}`` covers every enum. ``Literal``
    fields only ever match an exact ``Literal[...]`` key.
    """
    profiles = config.type_profiles
    if not profiles:
        return None
    if py_type in profiles:
        return tuple(profiles[py_type])
    if isinstance(py_type, type):
        for base in py_type.__mro__:
            if base in profiles:
                return tuple(profiles[base])
    return None


def _ops_for_spec(spec: FieldSpec, config: FilterConfig) -> tuple[Operator, ...]:
    """Resolve one field's operator set through the precedence layers.

    Finest wins (design doc 02 layering): route-level ``FilterConfig.operators``
    > field-level ``Filterable(ops=...)`` > ``FilterConfig.type_profiles`` >
    the global ``default_profile``. Explicit per-field/per-type lists bypass
    the ``allow_regex`` gate (listing ``regex`` is the eyes-open opt-in);
    ``ops.ALL`` and the default profile stay gated. Fields marked
    ``ops.NONE`` never reach this function — they are dropped from the
    filterable set in :func:`build_plan`.

    Array (``LIST``) fields resolve against the array operator profile;
    ``type_profiles`` entries never apply to them — a per-type profile lists
    *scalar* operators for a scalar type, not membership/shape operators for
    arrays of that element type.

    Raises :class:`ConfigurationError` when an explicitly configured operator
    is unknown or not valid for the field's type.
    """
    overrides = config.operators or {}
    field_ops = spec.filterable.ops if spec.filterable is not None else None
    if spec.public_name in overrides:
        names = _validated_ops(tuple(overrides[spec.public_name]), spec, "FilterConfig.operators")
    elif isinstance(field_ops, OpsMarker):
        # ops.ALL — everything the type supports, still regex-gated.
        names = all_operators_for(spec.py_type, spec.nullable, spec.container)
        if not config.allow_regex:
            names = tuple(n for n in names if n != "regex")
    elif field_ops is not None:
        names = _validated_ops(tuple(field_ops), spec, "Filterable(ops=...)")
    else:
        profile_names = (
            _match_type_profile(spec.py_type, config)
            if spec.container is Container.SCALAR
            else None
        )
        if profile_names is not None:
            # Validated for the type at config construction; drop the
            # nullable-only operators for non-nullable fields.
            valid = all_operators_for(spec.py_type, spec.nullable)
            names = tuple(n for n in profile_names if n in valid)
        else:
            names = operators_for(
                spec.py_type, spec.nullable, config.default_profile, spec.container
            )
            if not config.allow_regex:
                # `regex` is additionally config-gated even under the full profile.
                names = tuple(n for n in names if n != "regex")
    return tuple(DEFAULT_REGISTRY[n] for n in names)


def _value_annotation(spec: FieldSpec, op: Operator, config: FilterConfig) -> Any:
    """Build the Pydantic annotation driving coercion/validation for one param.

    ``spec.py_type`` is the element type for array fields, so ``has`` takes a
    single element value and ``has_any``/``has_all`` take element lists; the
    ``len__*`` operators are ``int``-valued regardless of the field type.
    """
    base: Any = int if op.value_type is ValueTypeRule.INT else spec.py_type
    if op.arity is Arity.BOOL:
        return Optional[bool]
    if op.arity is Arity.SINGLE:
        return Optional[base]
    if op.arity is Arity.LIST:
        length = Len(max_length=config.max_list_length)
    else:
        # RANGE (`between`): exactly two comma-joined (or repeated) values.
        length = Len(min_length=2, max_length=2)
    # Keep `Optional[list[T]]` as the outermost annotation so FastAPI's
    # sequence detection collects repeated query keys into a list.
    return Annotated[Optional[list[base]], BeforeValidator(_split_commas), length]


def _sort_checker(sortable: frozenset[str]) -> Any:
    """AfterValidator ensuring every sort token names a sortable field."""

    def check(value: str | None) -> str | None:
        if value is None:
            return value
        for token in value.split(","):
            token = token.strip()
            name = token[1:] if token.startswith("-") else token
            if not name:
                raise ValueError("empty sort field")
            if name not in sortable:
                raise ValueError(f"field {name!r} is not sortable")
        return value

    return AfterValidator(check)


def _max_filters_checker(filter_names: tuple[str, ...], max_filters: int) -> Any:
    """Model validator enforcing the ``max_filters`` guard per request."""

    def check(self: BaseModel) -> BaseModel:
        applied = sum(1 for name in filter_names if getattr(self, name) is not None)
        if applied > max_filters:
            raise ValueError(f"too many filters applied: {applied} (max_filters={max_filters})")
        return self

    return model_validator(mode="after")(check)


def _resolve_params(
    specs: tuple[FieldSpec, ...], config: FilterConfig
) -> tuple[ResolvedParam, ...]:
    """Emit every ``(url name, field, operator)`` triple, detecting collisions."""
    params: list[ResolvedParam] = []
    seen: dict[str, str] = {
        name: "(reserved pagination/sort parameter)" for name in RESERVED_PARAMS
    }
    for spec in specs:
        for op in _ops_for_spec(spec, config):
            names = [f"{spec.public_name}{config.separator}{op.name}"]
            if op.name == "eq":
                # Bare equality: `?name=x` is sugar for `name__eq=x`.
                names.append(spec.public_name)
            for url_name in names:
                origin = f"field {spec.public_name!r}, operator {op.name!r}"
                if url_name in seen:
                    raise ConfigurationError(
                        f"generated parameter name collision on {url_name!r}: "
                        f"{origin} conflicts with {seen[url_name]}"
                    )
                seen[url_name] = origin
                params.append(
                    ResolvedParam(
                        url_name=url_name,
                        python_name=_python_name(url_name),
                        spec=spec,
                        operator=op,
                    )
                )
    return tuple(params)


def _validate_config_fields(
    model: type[BaseModel], specs: tuple[FieldSpec, ...], config: FilterConfig
) -> None:
    """Reject config that names unknown, non-filterable, or opted-out fields.

    Config entries are keyed by *public* parameter names — a field renamed
    with ``Filterable(param=...)`` is referenced by its param name, an
    aliased field by its alias.
    """
    known = public_field_names(model) | {s.public_name for s in specs}
    filterable = {s.public_name for s in specs if not _is_unfilterable(s)}
    unfilterable = {s.public_name for s in specs if _is_unfilterable(s)}
    for name in config.exclude:
        if name not in known:
            raise ConfigurationError(
                f"unknown field {name!r} in FilterConfig.exclude for model "
                f"{model.__name__}; known fields: {', '.join(sorted(known))}"
            )
    for name in config.operators or {}:
        if name in unfilterable:
            raise ConfigurationError(
                f"field {name!r} of model {model.__name__} is marked "
                f"Filterable(ops=ops.NONE) and cannot be configured in "
                f"FilterConfig.operators; remove the marker to make it filterable"
            )
        if name not in filterable:
            raise ConfigurationError(
                f"unknown or non-filterable field {name!r} in FilterConfig.operators "
                f"for model {model.__name__}; filterable fields: "
                f"{', '.join(sorted(filterable))}"
            )


def _resolve_sortable(
    model: type[BaseModel],
    specs: tuple[FieldSpec, ...],
    filterable_specs: tuple[FieldSpec, ...],
    config: FilterConfig,
) -> frozenset[str]:
    """The sortable field set: config allow-list × per-field overrides.

    With no ``FilterConfig.sortable`` allow-list, the default is "sortable
    iff filterable" for scalar fields — array fields are *not* sortable by
    default (sorting on Mongo array fields uses min/max element semantics,
    which surprises people) — plus fields forced in with
    ``Filterable(sortable=True)`` (which can make an ``ops.NONE`` field
    sort-only, or an array field sortable, eyes open), minus fields opted
    out with ``Filterable(sortable=False)``. When the allow-list *is* given
    it wins (it may name array fields) — except ``sortable=False``, which is
    final and turns a conflicting allow-list entry into a
    :class:`ConfigurationError`.
    """
    flags = {s.public_name: s.filterable.sortable for s in specs if s.filterable is not None}
    non_sortable = {name for name, flag in flags.items() if flag is False}
    if config.sortable is None:
        forced = {name for name, flag in flags.items() if flag is True}
        base = {s.public_name for s in filterable_specs if s.container is Container.SCALAR}
        return frozenset((base | forced) - non_sortable)
    known = {s.public_name for s in specs}
    for name in config.sortable:
        if name in non_sortable:
            raise ConfigurationError(
                f"field {name!r} in FilterConfig.sortable is marked "
                f"Filterable(sortable=False) on model {model.__name__} and cannot "
                f"be made sortable"
            )
        if name not in known:
            raise ConfigurationError(
                f"field {name!r} in FilterConfig.sortable is not a filterable field "
                f"of model {model.__name__}; sortable candidates: "
                f"{', '.join(sorted(known - non_sortable))}"
            )
    return frozenset(config.sortable)


def _build_params_model(
    model: type[BaseModel],
    params: tuple[ResolvedParam, ...],
    sortable: frozenset[str],
    config: FilterConfig,
) -> type[BaseModel]:
    """Assemble the dynamic Pydantic query-parameter model via ``create_model``."""
    field_defs: dict[str, Any] = {}
    for p in params:
        annotation = _value_annotation(p.spec, p.operator, config)
        description = f"Filter: `{p.spec.public_name}` {_OP_HELP[p.operator.name]}."
        field_defs[p.python_name] = (
            annotation,
            Field(None, alias=p.url_name, description=description),
        )
    field_defs["limit"] = (
        int,
        Field(
            config.default_limit,
            ge=1,
            le=config.max_limit,
            description=f"Maximum number of items to return (max {config.max_limit}).",
        ),
    )
    field_defs["offset"] = (
        int,
        Field(0, ge=0, description="Number of items to skip."),
    )
    field_defs["sort"] = (
        Annotated[Optional[str], _sort_checker(sortable)],
        Field(
            None,
            description=(
                "Comma-separated sort fields; prefix a field with `-` for descending. "
                f"Sortable fields: {', '.join(sorted(sortable)) or '(none)'}."
            ),
        ),
    )
    filter_names = tuple(p.python_name for p in params)
    validators = {
        "_check_max_filters": _max_filters_checker(filter_names, config.max_filters),
    }
    return create_model(
        f"{model.__name__}FilterParams",
        __config__=ConfigDict(populate_by_name=True, extra="ignore"),
        __validators__=validators,
        **field_defs,
    )


def build_plan(model: type[BaseModel], config: FilterConfig) -> QueryPlan:
    """Derive (and memoize) the full query plan for a ``(model, config)`` pair.

    All configuration errors — unknown fields, invalid operators, unsortable
    sort fields, parameter-name collisions — are raised here, at registration.
    """
    key = (model, config)
    cached = _PLAN_CACHE.get(key)
    if cached is not None:
        return cached
    all_specs = introspect_model(model)
    _validate_config_fields(model, all_specs, config)
    visible = tuple(s for s in all_specs if s.public_name not in set(config.exclude))
    filterable_specs = tuple(s for s in visible if not _is_unfilterable(s))
    params = _resolve_params(filterable_specs, config)
    sortable = _resolve_sortable(model, visible, filterable_specs, config)
    params_model = _build_params_model(model, params, sortable, config)
    plan = QueryPlan(
        model=model,
        config=config,
        params=params,
        params_model=params_model,
        sortable=sortable,
        # Keyed by public name for every visible field (including sort-only
        # ops.NONE fields) so sort tokens always map to their source name.
        sources={s.public_name: s.source for s in visible},
        known_params=frozenset(p.url_name for p in params) | frozenset(RESERVED_PARAMS),
    )
    _PLAN_CACHE[key] = plan
    return plan
