"""Pydantic model introspection: model → tuple of :class:`FieldSpec`.

Resolving ``Optional``, aliases, enums, and (later) containers happens here,
once, at registration time. Stage 1 handles scalar fields and ``Optional``
scalars only; unsupported fields are silently skipped (they simply are not
filterable yet).
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from .operators import Container, type_kind

__all__ = ["FieldSpec", "introspect_model", "public_field_names"]


@dataclass(frozen=True)
class FieldSpec:
    """One filterable field, resolved at registration time.

    Attributes:
        path: Public name path; a single segment for top-level scalar fields
            (nested paths arrive in a later stage).
        source: Backend field name the compiled query uses (the Pydantic
            alias when one is set, else the field name).
        py_type: Resolved, Optional-unwrapped base type (or ``Literal`` form).
        container: Container shape; always ``SCALAR`` in Stage 1.
        nullable: Whether the field accepts ``None`` (drives ``isnull``).
        annotations: Reserved for ``Filterable`` metadata (a later stage).
    """

    path: tuple[str, ...]
    source: str
    py_type: Any
    container: Container
    nullable: bool
    annotations: object | None = None

    @property
    def public_name(self) -> str:
        """The name clients use in query parameters."""
        return "__".join(self.path)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Split ``Optional[T]`` / ``T | None`` into ``(T, nullable)``.

    Non-optional multi-arm unions are returned unchanged (and will be skipped
    downstream because ``type_kind`` rejects them).
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return annotation, False
    return annotation, False


def public_field_names(model: type[BaseModel]) -> frozenset[str]:
    """The public (alias-respecting) names of *all* fields on a model."""
    return frozenset(info.alias or name for name, info in model.model_fields.items())


def introspect_model(model: type[BaseModel]) -> tuple[FieldSpec, ...]:
    """Walk ``model_fields`` and return specs for every filterable field.

    Respects Pydantic aliases: the alias (when set) is both the public
    query-parameter name and the backend source name. Fields whose type is
    not a supported scalar are skipped.
    """
    specs: list[FieldSpec] = []
    for name, info in model.model_fields.items():
        public = info.alias or name
        base, nullable = _unwrap_optional(info.annotation)
        if type_kind(base) is None:
            continue
        specs.append(
            FieldSpec(
                path=(public,),
                source=public,
                py_type=base,
                container=Container.SCALAR,
                nullable=nullable,
            )
        )
    return tuple(specs)
