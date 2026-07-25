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
from .introspection import FieldSpec, introspect_model, public_field_names
from .operators import DEFAULT_REGISTRY, Arity, Operator, all_operators_for, operators_for

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


def _ops_for_spec(spec: FieldSpec, config: FilterConfig) -> tuple[Operator, ...]:
    """Resolve the operator set for one field, honoring per-field overrides.

    Raises :class:`ConfigurationError` when an explicitly configured operator
    is unknown or not valid for the field's type.
    """
    overrides = dict(config.operators or {})
    valid = all_operators_for(spec.py_type, spec.nullable)
    if spec.public_name in overrides:
        names = tuple(overrides[spec.public_name])
        for op_name in names:
            if op_name not in DEFAULT_REGISTRY:
                raise ConfigurationError(
                    f"unknown operator {op_name!r} configured for field {spec.public_name!r}"
                )
            if op_name not in valid:
                raise ConfigurationError(
                    f"operator {op_name!r} is not valid for field {spec.public_name!r} "
                    f"(type {spec.py_type!r}); valid operators: {', '.join(valid)}"
                )
    else:
        names = operators_for(spec.py_type, spec.nullable, config.default_profile)
        if not config.allow_regex:
            # `regex` is additionally config-gated even under the full profile.
            names = tuple(n for n in names if n != "regex")
    return tuple(DEFAULT_REGISTRY[n] for n in names)


def _value_annotation(spec: FieldSpec, op: Operator, config: FilterConfig) -> Any:
    """Build the Pydantic annotation driving coercion/validation for one param."""
    base: Any = spec.py_type
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
    """Reject config that names unknown or non-filterable fields."""
    model_publics = public_field_names(model)
    filterable = {s.public_name for s in specs}
    for name in config.exclude:
        if name not in model_publics:
            raise ConfigurationError(
                f"unknown field {name!r} in FilterConfig.exclude for model {model.__name__}"
            )
    for name in config.operators or {}:
        if name not in filterable:
            raise ConfigurationError(
                f"unknown or non-filterable field {name!r} in FilterConfig.operators "
                f"for model {model.__name__}"
            )


def _resolve_sortable(
    model: type[BaseModel], specs: tuple[FieldSpec, ...], config: FilterConfig
) -> frozenset[str]:
    """The sortable allow-list; defaults to the filterable field set."""
    filterable = {s.public_name for s in specs}
    if config.sortable is None:
        return frozenset(filterable)
    for name in config.sortable:
        if name not in filterable:
            raise ConfigurationError(
                f"field {name!r} in FilterConfig.sortable is not a filterable field "
                f"of model {model.__name__}"
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
    specs = tuple(s for s in all_specs if s.public_name not in set(config.exclude))
    params = _resolve_params(specs, config)
    sortable = _resolve_sortable(model, specs, config)
    params_model = _build_params_model(model, params, sortable, config)
    plan = QueryPlan(
        model=model,
        config=config,
        params=params,
        params_model=params_model,
        sortable=sortable,
        sources={s.public_name: s.source for s in specs},
    )
    _PLAN_CACHE[key] = plan
    return plan
