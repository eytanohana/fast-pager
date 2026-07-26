"""Pydantic model introspection: model → tuple of :class:`FieldSpec`.

Resolving ``Optional``, aliases, enums, :class:`~fast_pager.Filterable`
metadata, and containers happens here, once, at registration time. Scalar
fields, ``list[T]``/``set[T]`` of supported scalars, **nested Pydantic
models** (recursively, to a configurable depth), and their ``Optional``
wrappers are supported; anything else (``dict``, arrays of nested models —
later stages) is silently skipped (it simply is not filterable yet) — unless
it carries an explicit ``Filterable`` annotation, which is a configuration
error.

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

Nested models compose both names segment by segment: the public path joins
with the separator (``address__city``), the source path with dots
(``address.city``), and a ``param``/``source``/alias override on any segment
— the embedding field included — renames exactly that segment.

Depth bound (design doc 02, *Parameter matching*): ``max_depth`` counts the
embedded-model boundaries between the root model and a field. Fields more
than ``max_depth`` boundaries below the root are silently skipped; a nested
model sitting *exactly* at the bound still yields its own spec (so a
nullable embedding keeps ``isnull``) but none of its children. The bound
also truncates self-referential and mutually-recursive models — recursion
depth strictly increases, so cycles terminate at the bound by construction.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .errors import ConfigurationError
from .filterable import Filterable, OpsMarker
from .operators import Container, type_kind, type_name

__all__ = ["FieldSpec", "introspect_model", "public_field_names"]

#: Default number of nested-model levels below the root that introspection
#: descends into (design doc 02: "default 2 levels").
DEFAULT_MAX_DEPTH = 2


@dataclass(frozen=True)
class FieldSpec:
    """One filterable field, resolved at registration time.

    Attributes:
        path: Public name path, one segment per model level:
            ``("age",)`` for a top-level field, ``("address", "city")`` for
            a field of a nested model.
        source: Backend field name the compiled query uses; nested paths are
            dotted (``"address.city"``). Each segment resolves independently
            (``Filterable(source=...)``, else the Pydantic alias, else the
            field name).
        py_type: Resolved, Optional-unwrapped base type (or ``Literal``
            form); the *element* type for ``LIST`` fields, the nested model
            class for ``NESTED`` fields.
        container: Container shape; ``SCALAR``, ``LIST`` for
            ``list[T]``/``set[T]`` of supported scalars, or ``NESTED`` for a
            field embedding another Pydantic model.
        nullable: Whether the field accepts ``None`` (drives ``isnull``).
        filterable: The field's ``Filterable`` annotation, when present.
        separator: The token joining ``path`` segments into the public name.
    """

    path: tuple[str, ...]
    source: str
    py_type: Any
    container: Container
    nullable: bool
    filterable: Filterable | None = None
    separator: str = "__"

    @property
    def public_name(self) -> str:
        """The name clients use in query parameters."""
        return self.separator.join(self.path)


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


def _list_element(annotation: Any) -> Any | None:
    """The element type of a ``list[T]``/``set[T]`` of supported scalars.

    Returns ``None`` for anything else — bare ``list``, other container
    origins, and element types that are not filterable scalars (e.g. nested
    models, ``list[str | None]``) all stay unsupported for now.
    """
    if get_origin(annotation) not in (list, set):
        return None
    args = get_args(annotation)
    if len(args) == 1 and type_kind(args[0]) is not None:
        return args[0]
    return None


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """The nested model class when the annotation embeds one, else ``None``."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


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


def introspect_model(
    model: type[BaseModel],
    *,
    separator: str = "__",
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[FieldSpec, ...]:
    """Walk ``model_fields`` and return specs for every filterable field.

    Public and source names follow the module-level naming rules (``param`` /
    alias / field name and ``source`` / alias / field name respectively),
    composed segment by segment for nested models; ``separator`` joins the
    public path, ``max_depth`` bounds the nested-model recursion (see the
    module docstring for the precise semantics). Fields whose type is not
    supported are skipped — unless they carry a ``Filterable`` annotation,
    which raises :class:`~fast_pager.errors.ConfigurationError` naming the
    field and type (silently ignoring explicit metadata would hide a
    misconfiguration).
    """
    specs: list[FieldSpec] = []
    _walk(model, (), (), specs, separator=separator, max_depth=max_depth)
    return tuple(specs)


def _walk(
    model: type[BaseModel],
    path: tuple[str, ...],
    source_path: tuple[str, ...],
    specs: list[FieldSpec],
    *,
    separator: str,
    max_depth: int,
) -> None:
    """Append the specs of one model level, recursing into nested models."""
    for name, info in model.model_fields.items():
        filterable = _filterable_metadata(model, name, info)
        base, nullable = _unwrap_optional(info.annotation)
        default_name = info.alias or name
        public = filterable.param if filterable is not None and filterable.param else default_name
        source = filterable.source if filterable is not None and filterable.source else default_name
        field_path = (*path, public)
        source_name = ".".join((*source_path, source))
        nested = _nested_model(base)
        if nested is not None:
            # The embedding field itself: only nullability operators apply,
            # but the spec always exists so exclude/sortable/ops.NONE can
            # target the subtree by its public name.
            specs.append(
                FieldSpec(
                    path=field_path,
                    source=source_name,
                    py_type=nested,
                    container=Container.NESTED,
                    nullable=nullable,
                    filterable=filterable,
                    separator=separator,
                )
            )
            opted_out = filterable is not None and filterable.ops is OpsMarker.NONE
            if not opted_out and len(field_path) <= max_depth:
                _walk(
                    nested,
                    field_path,
                    (*source_path, source),
                    specs,
                    separator=separator,
                    max_depth=max_depth,
                )
            continue
        element = _list_element(base)
        if element is not None:
            py_type, container = element, Container.LIST
        elif type_kind(base) is not None:
            py_type, container = base, Container.SCALAR
        else:
            if filterable is not None:
                raise ConfigurationError(
                    f"field {name!r} of model {model.__name__} is annotated with "
                    f"Filterable but its type {type_name(base)} is not a supported "
                    f"filterable type"
                )
            continue
        specs.append(
            FieldSpec(
                path=field_path,
                source=source_name,
                py_type=py_type,
                container=container,
                nullable=nullable,
                filterable=filterable,
                separator=separator,
            )
        )
