"""Pydantic model introspection: model → tuple of :class:`FieldSpec`.

Resolving ``Optional``, aliases, enums, :class:`~fast_pager.Filterable`
metadata, and containers happens here, once, at registration time. Scalar
fields, ``list[T]``/``set[T]`` of supported scalars, **nested Pydantic
models** (recursively, to a configurable depth), **arrays of nested models**
(``list[NestedModel]``, whose element fields appear under an ``elem`` path
segment), **maps** (``dict[str, T]``, only when explicitly enabled with a
``Filterable`` annotation), and their ``Optional`` wrappers are supported;
anything else is silently skipped (it simply is not filterable) — unless it
carries an explicit ``Filterable`` annotation, which is a configuration
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

Arrays of nested models (design doc 02, ``list[NestedModel]``): the element
model's fields are walked exactly like an embedding's, but under a literal
``elem`` path segment — ``orders__elem__amount`` — whose source path carries
the ``$elem`` marker (``orders.$elem.amount``) that the Mongo compiler
groups into a single ``$elemMatch`` per array field. The ``elem`` hop counts
as **one** embedded-model boundary toward ``max_depth``, exactly like an
embedding (the ``elem`` token itself adds no extra level). Element fields
are flagged ``in_element`` — they are never sortable and their parameters
are ``full``-tier (gated in :mod:`fast_pager.params`).

Maps (design doc 02, free-form maps): a ``dict[str, T]`` field is **not
filterable by default** — it yields specs only when it carries a
``Filterable`` annotation. The map field itself becomes a ``MAP`` spec
(``has_key``); each key enumerated in ``Filterable(keys=[...])``
additionally yields a scalar value-at-key spec (``metadata__region`` →
source ``metadata.region``), flagged ``is_map_value``. A ``Filterable`` on a
map with a non-``str`` key type or an unsupported value type is a
configuration error.

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

from .ast import ELEM_SOURCE_MARKER
from .errors import ConfigurationError
from .filterable import Filterable, OpsMarker
from .operators import Container, type_kind, type_name

__all__ = ["ELEM_SEGMENT", "FieldSpec", "introspect_model", "public_field_names"]

#: Default number of nested-model levels below the root that introspection
#: descends into (design doc 02: "default 2 levels").
DEFAULT_MAX_DEPTH = 2

#: The public path segment naming "an element of this array" in generated
#: parameter names: ``orders__elem__amount``. Its source-path counterpart is
#: :data:`fast_pager.ast.ELEM_SOURCE_MARKER`.
ELEM_SEGMENT = "elem"


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
            class for ``NESTED`` and ``LIST_OF_NESTED`` fields, the *value*
            type for ``MAP`` fields (and their value-at-key specs).
        container: Container shape; ``SCALAR``, ``LIST`` for
            ``list[T]``/``set[T]`` of supported scalars, ``NESTED`` for a
            field embedding another Pydantic model, ``LIST_OF_NESTED`` for
            ``list[NestedModel]``, or ``MAP`` for an enabled ``dict[str, T]``.
        nullable: Whether the field accepts ``None`` (drives ``isnull``).
        filterable: The field's ``Filterable`` annotation, when present.
        separator: The token joining ``path`` segments into the public name.
        in_element: Whether the field lives inside a ``list[NestedModel]``
            element (its path crosses an ``elem`` segment). Element fields
            are never sortable, and their parameters are ``full``-tier.
        is_map_value: Whether this spec is a value-at-key parameter of a map
            field (``Filterable(keys=[...])``); such specs expose ``eq`` only.
    """

    path: tuple[str, ...]
    source: str
    py_type: Any
    container: Container
    nullable: bool
    filterable: Filterable | None = None
    separator: str = "__"
    in_element: bool = False
    is_map_value: bool = False

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


def _list_of_nested(annotation: Any) -> type[BaseModel] | None:
    """The element model class of a ``list[NestedModel]``, else ``None``."""
    if get_origin(annotation) is not list:
        return None
    args = get_args(annotation)
    return _nested_model(args[0]) if len(args) == 1 else None


def _map_value(annotation: Any) -> Any | None:
    """The value type of a supported ``dict[str, T]`` map, else ``None``.

    Supported means: the key type is exactly ``str`` and the value type is a
    filterable scalar. Bare ``dict`` and every other shape stay unsupported.
    """
    if get_origin(annotation) is not dict:
        return None
    args = get_args(annotation)
    if len(args) == 2 and args[0] is str and type_kind(args[1]) is not None:
        return args[1]
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
    depth: int = 0,
    in_element: bool = False,
) -> None:
    """Append the specs of one model level, recursing into nested models.

    ``depth`` is the number of embedded-model boundaries already crossed —
    the paths produced here may be longer than that (the ``elem`` segment is
    a token, not a boundary of its own), so depth is tracked explicitly
    rather than derived from the path length.
    """
    for name, info in model.model_fields.items():
        filterable = _filterable_metadata(model, name, info)
        base, nullable = _unwrap_optional(info.annotation)
        default_name = info.alias or name
        public = filterable.param if filterable is not None and filterable.param else default_name
        source = filterable.source if filterable is not None and filterable.source else default_name
        field_path = (*path, public)
        source_name = ".".join((*source_path, source))
        is_dict = base is dict or get_origin(base) is dict
        if filterable is not None and filterable.keys is not None and not is_dict:
            raise ConfigurationError(
                f"field {name!r} of model {model.__name__} has Filterable(keys=...) "
                f"but its type {type_name(base)} is not a dict[str, T] map"
            )
        opted_out = filterable is not None and filterable.ops is OpsMarker.NONE
        common: dict[str, Any] = {
            "path": field_path,
            "source": source_name,
            "nullable": nullable,
            "filterable": filterable,
            "separator": separator,
            "in_element": in_element,
        }
        nested = _nested_model(base)
        if nested is not None:
            # The embedding field itself: only nullability operators apply,
            # but the spec always exists so exclude/sortable/ops.NONE can
            # target the subtree by its public name.
            specs.append(FieldSpec(py_type=nested, container=Container.NESTED, **common))
            if not opted_out and depth + 1 <= max_depth:
                _walk(
                    nested,
                    field_path,
                    (*source_path, source),
                    specs,
                    separator=separator,
                    max_depth=max_depth,
                    depth=depth + 1,
                    in_element=in_element,
                )
            continue
        elem_model = _list_of_nested(base)
        if elem_model is not None:
            # The array field itself exposes only shape (and nullability)
            # operators; element-level filtering goes through the `elem`
            # paths below. The element hop is one model boundary, exactly
            # like an embedding.
            specs.append(
                FieldSpec(py_type=elem_model, container=Container.LIST_OF_NESTED, **common)
            )
            if not opted_out and depth + 1 <= max_depth:
                _walk(
                    elem_model,
                    (*field_path, ELEM_SEGMENT),
                    (*source_path, source, ELEM_SOURCE_MARKER),
                    specs,
                    separator=separator,
                    max_depth=max_depth,
                    depth=depth + 1,
                    in_element=True,
                )
            continue
        if is_dict:
            if filterable is None:
                # Maps are not filterable unless explicitly enabled
                # (design doc 02, free-form maps).
                continue
            value = _map_value(base)
            if value is None:
                raise ConfigurationError(
                    f"field {name!r} of model {model.__name__} is annotated with "
                    f"Filterable but its type {type_name(base)} is not a supported "
                    f"map type; maps must be dict[str, T] with a filterable scalar T"
                )
            specs.append(FieldSpec(py_type=value, container=Container.MAP, **common))
            if not opted_out:
                for key in filterable.keys or ():
                    # A value-at-key parameter: `metadata__region` filtering
                    # the dotted path `metadata.region`, typed as the map's
                    # value type, `eq` only. The Filterable metadata belongs
                    # to the map field, not to its key specs.
                    specs.append(
                        FieldSpec(
                            path=(*field_path, key),
                            source=f"{source_name}.{key}",
                            py_type=value,
                            container=Container.SCALAR,
                            nullable=False,
                            separator=separator,
                            in_element=in_element,
                            is_map_value=True,
                        )
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
        specs.append(FieldSpec(py_type=py_type, container=container, **common))
