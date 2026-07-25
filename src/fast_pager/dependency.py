"""`FilterDepends`: the FastAPI dependency wiring for a filter surface."""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin

from fastapi import Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from .config import FilterConfig
from .errors import ConfigurationError
from .params import QueryPlan, build_plan
from .query import FilterQuery

__all__ = ["FilterDepends"]


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


def FilterDepends(target: Any, *, config: FilterConfig | None = None) -> Any:
    """Create the FastAPI dependency for a model's filter/sort/page surface.

    Accepts the resource model directly (``FilterDepends(User)``) or the
    typed alias (``FilterDepends(FilterQuery[User])``); both yield the same
    :class:`~fast_pager.query.FilterQuery` object at request time.

    Parameter generation, config validation, and collision detection all run
    *here*, at route-registration time — misconfiguration raises
    :class:`~fast_pager.errors.ConfigurationError` before the app serves
    traffic.
    """
    model = _resolve_model(target)
    plan = build_plan(model, config if config is not None else FilterConfig())

    def dependency(request: Request, raw: BaseModel) -> FilterQuery[Any]:
        if plan.config.unknown_params == "strict":
            _check_unknown_params(plan, request)
        return FilterQuery(plan, raw)

    # `from __future__ import annotations` stringifies def-time annotations,
    # and the dynamic model is not resolvable by name anyway — assign the
    # real annotation object so FastAPI sees the query-parameter model.
    dependency.__annotations__["raw"] = Annotated[plan.params_model, Query()]
    dependency.__name__ = f"filter_{model.__name__}"
    return Depends(dependency)
