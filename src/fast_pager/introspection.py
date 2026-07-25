"""Pydantic model introspection: model → tuple of :class:`FieldSpec`.

Resolving ``Optional``, aliases, enums, :class:`~fast_pager.Filterable`
metadata, and (later) containers happens here, once, at registration time.
Stage 2 handles scalar fields and ``Optional`` scalars; unsupported fields
are silently skipped (they simply are not filterable yet) — unless they
carry an explicit ``Filterable`` annotation, which is a configuration error.

Naming (design doc 02, *Field → DB-name mapping and aliases*): each field
has two names, resolved independently —

- the **public** name clients use in query parameters:
  ``Filterable(param=...)`` if set, else the Pydantic alias, else the field
  name;
- the **source** name the compiled backend query uses:
  ``Filterable(source=...)`` if set, else the Pydantic alias, else the field
  name.

A Pydantic alias therefore stays the default for *both* (Stage 1 behavior),
and ``param``/``source`` pull the two apart when the URL and the database
disagree with the model.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .errors import ConfigurationError
from .filterable import Filterable
from .operators import Container, type_kind, type_name

__all__ = ["FieldSpec", "introspect_model", "public_field_names"]


@dataclass(frozen=True)
class FieldSpec:
    """One filterable field, resolved at registration time.

    Attributes:
        path: Public name path; a single segment for top-level scalar fields
            (nested paths arrive in a later stage).
        source: Backend field name the compiled query uses
            (``Filterable(source=...)``, else the Pydantic alias, else the
            field name).
        py_type: Resolved, Optional-unwrapped base type (or ``Literal`` form).
        container: Container shape; always ``SCALAR`` in Stage 2.
        nullable: Whether the field accepts ``None`` (drives ``isnull``).
        filterable: The field's ``Filterable`` annotation, when present.
    """

    path: tuple[str, ...]
    source: str
    py_type: Any
    container: Container
    nullable: bool
    filterable: Filterable | None = None

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


def _filterable_metadata(model: type[BaseModel], name: str, info: FieldInfo) -> Filterable | None:
    """Extract a field's ``Filterable`` annotation, rejecting duplicates."""
    found = [m for m in info.metadata if isinstance(m, Filterable)]
    if len(found) > 1:
        raise ConfigurationError(
            f"field {name!r} of model {model.__name__} has {len(found)} Filterable "
            f"annotations; declare at most one"
        )
    return found[0] if found else None


def public_field_names(model: type[BaseModel]) -> frozenset[str]:
    """The public (alias-respecting) names of *all* fields on a model."""
    return frozenset(info.alias or name for name, info in model.model_fields.items())


def introspect_model(model: type[BaseModel]) -> tuple[FieldSpec, ...]:
    """Walk ``model_fields`` and return specs for every filterable field.

    Public and source names follow the module-level naming rules (``param`` /
    alias / field name and ``source`` / alias / field name respectively).
    Fields whose type is not a supported scalar are skipped — unless they
    carry a ``Filterable`` annotation, which raises
    :class:`~fast_pager.errors.ConfigurationError` naming the field and type
    (silently ignoring explicit metadata would hide a misconfiguration).
    """
    specs: list[FieldSpec] = []
    for name, info in model.model_fields.items():
        filterable = _filterable_metadata(model, name, info)
        base, nullable = _unwrap_optional(info.annotation)
        if type_kind(base) is None:
            if filterable is not None:
                raise ConfigurationError(
                    f"field {name!r} of model {model.__name__} is annotated with "
                    f"Filterable but its type {type_name(base)} is not a supported "
                    f"filterable type"
                )
            continue
        default_name = info.alias or name
        public = filterable.param if filterable is not None and filterable.param else default_name
        source = filterable.source if filterable is not None and filterable.source else default_name
        specs.append(
            FieldSpec(
                path=(public,),
                source=source,
                py_type=base,
                container=Container.SCALAR,
                nullable=nullable,
                filterable=filterable,
            )
        )
    return tuple(specs)
