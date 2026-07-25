"""`FilterDepends`: the FastAPI dependency wiring for a filter surface."""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin

from fastapi import Depends, Query
from pydantic import BaseModel

from .config import FilterConfig
from .errors import ConfigurationError
from .params import build_plan
from .query import FilterQuery

__all__ = ["FilterDepends"]


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

    def dependency(raw: BaseModel) -> FilterQuery[Any]:
        return FilterQuery(plan, raw)

    # `from __future__ import annotations` stringifies def-time annotations,
    # and the dynamic model is not resolvable by name anyway — assign the
    # real annotation object so FastAPI sees the query-parameter model.
    dependency.__annotations__["raw"] = Annotated[plan.params_model, Query()]
    dependency.__name__ = f"filter_{model.__name__}"
    return Depends(dependency)
