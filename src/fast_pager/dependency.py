"""`FilterDepends`: the FastAPI dependency wiring for a filter surface."""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin

from fastapi import Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from .backends.base import QueryCompiler, capabilities_for_path
from .config import FilterConfig
from .errors import ConfigurationError
from .filterset import FilterSet, filterset_plan
from .params import QueryPlan, build_plan
from .query import FilterQuery

__all__ = ["FilterDepends"]


def _validate_backend(plan: QueryPlan, backend: QueryCompiler, surface: str) -> None:
    """Intersect the generated parameter surface with the backend's declaration.

    The registration-time half of "never silently drop a filter" (design doc
    04): every generated ``(field, operator)`` parameter must use an operator
    in ``backend.supported_ops``, and its source path must only need
    capabilities in ``backend.capabilities`` — otherwise the route fails at
    startup with every offending parameter named, instead of a request-time
    :class:`~fast_pager.errors.CompilationError`.
    """
    problems: list[str] = []
    for p in plan.params:
        op = p.operator.name
        if op not in backend.supported_ops:
            problems.append(f"parameter {p.url_name!r} uses unsupported operator {op!r}")
            continue
        missing = capabilities_for_path(p.spec.source) - backend.capabilities
        if missing:
            needs = ", ".join(sorted(capability.value for capability in missing))
            problems.append(f"parameter {p.url_name!r} (source {p.spec.source!r}) needs {needs}")
    if problems:
        raise ConfigurationError(
            f"backend {backend.name!r} cannot serve the filter surface of {surface}: "
            + "; ".join(problems)
            + ". Remove these parameters (Filterable(ops=...), FilterSet fields, or "
            "FilterConfig.operators/exclude) or choose a backend that supports them."
        )


def _check_unknown_params(plan: QueryPlan, request: Request) -> None:
    """Reject unrecognized filter parameters (``unknown_params="strict"``).

    The exact rule: a query-parameter name triggers a standard 422 when it

    1. is **not** one of the generated (or reserved) parameter names, **and**
    2. **contains the configured separator** — i.e. it claims to be a
       ``field__op`` filter (design doc 01, *Errors and ergonomics*).

    Names *without* the separator are never rejected, because the route (or
    another dependency) may legitimately declare them — this dependency
    cannot see its siblings' parameters. Corollary: when strict mode is on,
    a route's own query parameters must not contain the separator, or
    requests using them will be rejected here.
    """
    separator = plan.config.separator
    errors = [
        {
            "type": "unrecognized_filter",
            "loc": ("query", name),
            "msg": f"Unrecognized filter parameter {name!r}",
            "input": request.query_params[name],
        }
        # dict.fromkeys: unique names, in request order.
        for name in dict.fromkeys(request.query_params)
        if separator in name and name not in plan.known_params
    ]
    if errors:
        # FastAPI's default handler turns this into the standard 422 shape.
        raise RequestValidationError(errors)


def _resolve_model(target: Any) -> type[BaseModel]:
    """Accept a Pydantic model class or a ``FilterQuery[Model]`` alias."""
    if get_origin(target) is FilterQuery:
        args = get_args(target)
        if len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0]
        raise ConfigurationError(
            f"FilterDepends(FilterQuery[...]) requires a Pydantic model argument, got {target!r}"
        )
    if isinstance(target, type) and issubclass(target, BaseModel):
        return target
    raise ConfigurationError(
        f"FilterDepends() expects a Pydantic model class or FilterQuery[Model], got {target!r}"
    )


def FilterDepends(
    target: Any,
    *,
    config: FilterConfig | None = None,
    backend: QueryCompiler | None = None,
) -> Any:
    """Create the FastAPI dependency for a model's filter/sort/page surface.

    Accepts the resource model directly (``FilterDepends(User)``), the typed
    alias (``FilterDepends(FilterQuery[User])``), or a
    :class:`~fast_pager.filterset.FilterSet` subclass
    (``FilterDepends(UserFilter)``); every form yields the same
    :class:`~fast_pager.query.FilterQuery` object at request time, so call
    sites never change when a model graduates between them.

    Parameter generation, config validation, and collision detection all run
    at registration time — misconfiguration raises
    :class:`~fast_pager.errors.ConfigurationError` before the app serves
    traffic (a FilterSet validates even earlier, at class definition). A
    FilterSet carries its own configuration in ``Meta.config``, so passing
    ``config=`` alongside one is a :class:`ConfigurationError` rather than a
    silent tiebreak.

    ``backend=`` is an *optional* validation hook (design doc 04's
    registration-time intersection): pass the compiler the route will
    actually run on (e.g. ``SQLAlchemyCompiler(UserRow)``) and the generated
    parameter surface is checked against its ``supported_ops`` and
    ``capabilities`` — a parameter the backend cannot compile raises
    :class:`ConfigurationError` at startup, naming every offending
    parameter, instead of failing at request time. It does not change what
    the dependency returns; ``q`` stays backend-agnostic.
    """
    if isinstance(target, type) and issubclass(target, FilterSet):
        if config is not None:
            raise ConfigurationError(
                f"FilterDepends({target.__name__}, config=...) is ambiguous: a "
                f"FilterSet carries its configuration in Meta.config; remove the "
                f"config argument"
            )
        plan = filterset_plan(target)
        name = target.__name__
    else:
        model = _resolve_model(target)
        plan = build_plan(model, config if config is not None else FilterConfig())
        name = model.__name__

    if backend is not None:
        _validate_backend(plan, backend, name)

    def dependency(request: Request, raw: BaseModel) -> FilterQuery[Any]:
        if plan.config.unknown_params == "strict":
            _check_unknown_params(plan, request)
        return FilterQuery(plan, raw)

    # `from __future__ import annotations` stringifies def-time annotations,
    # and the dynamic model is not resolvable by name anyway — assign the
    # real annotation object so FastAPI sees the query-parameter model.
    dependency.__annotations__["raw"] = Annotated[plan.params_model, Query()]
    dependency.__name__ = f"filter_{name}"
    return Depends(dependency)
