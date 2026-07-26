"""`FilterSet`: an allow-list filter surface declared apart from the model.

Design doc 01, Option B: keep the model a pure data model and declare the
filter surface in its own class::

    class UserFilter(FilterSet):
        class Meta:
            model = User
            fields = {
                "name": ["contains", "startswith"],
                "age":  ["gte", "lte"],
                # fields omitted here are NOT filterable
            }

        # a custom filter not derivable from a single generated name:
        active_since = Filter(field="last_login", op="gte")

    @app.get("/users")
    async def list_users(q: FilterQuery[User] = FilterDepends(UserFilter)):
        return await db.users.find(q.to_mongo()).to_list(None)

The ``fields`` mapping is a **strict allow-list** (design doc 02): a field
that is not listed generates no parameters at all — the deliberate security
posture for public APIs, where adding a model field must never silently
widen the filter surface. Keys use the public dotted-param spelling
(``"address__city"``, ``"orders__elem__amount"``); each value is an exact
operator list validated against the field's type, or the string
``"__all__"`` (equivalently ``ops.ALL``) for everything the field's type
supports — still subject to the ``allow_regex`` gate, exactly like
``Filterable(ops=ops.ALL)``.

On the doc 02 precedence ladder the mapping occupies **layer 4** — the same
layer as ``FilterConfig.operators``, which it replaces for FilterSet usage:
it beats field-level ``Filterable(ops=...)``, per-type ``type_profiles``,
and the global profile. The model-level absolutes stay absolute:
``Filterable(ops=ops.NONE)`` and ``Filterable(sortable=False)`` cannot be
overridden by a FilterSet, and listing such a field raises
:class:`~fast_pager.errors.ConfigurationError`.

Everything is validated **at class-definition time** — the plan (parameter
model included) is built once in ``__init_subclass__`` and stored on the
class, so a bad FilterSet fails at import and multiple FilterSets over the
same model coexist without touching each other's plans.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, ClassVar

from pydantic import BaseModel

from .config import FilterConfig
from .errors import ConfigurationError
from .filterable import OpsMarker
from .introspection import FieldSpec, introspect_model
from .operators import DEFAULT_REGISTRY, all_operators_for, type_name
from .params import (
    RESERVED_PARAMS,
    QueryPlan,
    ResolvedParam,
    _build_params_model,
    _is_unfilterable,
    _python_name,
    _resolve_sortable,
    _validated_ops,
)

__all__ = ["ALL_OPS", "Filter", "FilterSet"]

#: The ``Meta.fields`` value meaning "every operator this field's type
#: supports" (still subject to the ``allow_regex`` gate). ``ops.ALL`` is an
#: accepted synonym.
ALL_OPS = "__all__"

#: The attribute names a ``Meta`` inner class may define.
_META_KEYS = frozenset({"model", "fields", "config", "sortable"})


@dataclass(frozen=True)
class Filter:
    """A custom declared filter: one extra parameter on a :class:`FilterSet`.

    Declared as a class attribute; the attribute name is the public
    parameter name (``active_since = Filter(field="last_login", op="gte")``
    generates ``?active_since=...``). The value type derives from the target
    field and the operator, and the condition compiles through the normal
    AST path — ``?active_since=2024-01-01`` becomes
    ``Condition(field="last_login", op="gte", value=date(...))``.

    Attributes:
        field: The target field's *public* dotted-param spelling
            (``"last_login"``, ``"address__city"``). The field does not have
            to appear in ``Meta.fields`` — a declared filter is its own
            opt-in — but ``Filterable(ops=ops.NONE)`` fields stay final and
            raise :class:`~fast_pager.errors.ConfigurationError`.
        op: The operator name, validated against the field's type exactly
            like an entry in ``Meta.fields``.
        param: Optional public parameter name override; defaults to the
            attribute name.
        description: Optional OpenAPI description override for the
            generated parameter.
    """

    field: str
    op: str
    param: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        """Validate the shape of the declaration eagerly, at construction."""
        if not self.field or not isinstance(self.field, str):
            raise ConfigurationError("Filter(field=...) must be a non-empty public field name")
        if not self.op or not isinstance(self.op, str):
            raise ConfigurationError("Filter(op=...) must be a non-empty operator name")
        if self.param is not None and not self.param:
            raise ConfigurationError("Filter(param=...) must be a non-empty string")


class FilterSet:
    """Base class for declarative, allow-list filter surfaces (doc 01 Option B).

    Subclass it with a ``Meta`` inner class:

    - ``model`` (required): the Pydantic model the surface is derived from.
    - ``fields``: the strict allow-list mapping — public field path →
      operator list (or ``"__all__"``). Defaults to ``{}``: nothing but the
      declared :class:`Filter` attributes and pagination/sort.
    - ``config``: a :class:`~fast_pager.FilterConfig` carrying limits,
      profile, strict mode, ``allow_regex``, ``type_profiles``,
      ``separator``, ``max_depth``. Its ``operators`` / ``exclude`` /
      ``sortable`` knobs are **not allowed** here — the FilterSet spellings
      (``fields`` / omission / ``Meta.sortable``) replace them.
    - ``sortable``: optional sortable allow-list (public names). Without
      it, the default is "sortable iff listed in ``fields`` and scalar",
      plus ``Filterable(sortable=True)`` fields, minus
      ``Filterable(sortable=False)`` ones.

    A subclass without a ``Meta`` anywhere in its MRO is an *abstract*
    FilterSet: it may hold shared :class:`Filter` declarations but cannot be
    passed to ``FilterDepends``. Everything else is validated — and the
    parameter model built — at class-definition time.

    ``FilterDepends(UserFilter)`` yields the exact same
    :class:`~fast_pager.FilterQuery` object as the zero-config and
    ``Filterable`` paths, so call sites never change when a model graduates
    between them.
    """

    _fs_plan: ClassVar[QueryPlan]

    def __init__(self) -> None:
        """FilterSets are declarative-only; there is nothing to instantiate."""
        raise TypeError(
            f"{type(self).__name__} is a declarative FilterSet; pass the class to "
            f"FilterDepends({type(self).__name__}) instead of instantiating it"
        )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Build and validate the subclass's query plan at definition time."""
        super().__init_subclass__(**kwargs)
        if getattr(cls, "Meta", None) is not None:
            cls._fs_plan = _build_filterset_plan(cls)


def filterset_plan(filterset: type[FilterSet]) -> QueryPlan:
    """The plan built for a concrete FilterSet at class definition.

    Raises :class:`~fast_pager.errors.ConfigurationError` for an abstract
    FilterSet (no ``Meta``) — the plan lookup deliberately ignores inherited
    plans, because a subclass with a reachable ``Meta`` always builds its own.
    """
    plan = filterset.__dict__.get("_fs_plan")
    if isinstance(plan, QueryPlan):
        return plan
    raise ConfigurationError(
        f"FilterSet {filterset.__name__} has no Meta and cannot back a filter "
        f"surface; define `class Meta: model = ...` on it"
    )


def _parse_meta(cls: type[FilterSet]) -> tuple[type[BaseModel], dict[str, Any], FilterConfig]:
    """Validate the ``Meta`` inner class and return ``(model, fields, config)``.

    The returned config has ``Meta.sortable`` folded into
    ``FilterConfig.sortable`` so downstream sortable resolution reuses the
    standard machinery.
    """
    meta = getattr(cls, "Meta")  # presence checked by the caller  # noqa: B009
    unknown = sorted(k for k in vars(meta) if not k.startswith("_") and k not in _META_KEYS)
    if unknown:
        raise ConfigurationError(
            f"unknown Meta attribute(s) {', '.join(map(repr, unknown))} on FilterSet "
            f"{cls.__name__}; supported attributes: {', '.join(sorted(_META_KEYS))}"
        )
    model = getattr(meta, "model", None)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigurationError(
            f"FilterSet {cls.__name__} requires `Meta.model` to be a Pydantic model "
            f"class, got {model!r}"
        )
    fields = getattr(meta, "fields", {})
    if not isinstance(fields, dict):
        raise ConfigurationError(
            f"`Meta.fields` on FilterSet {cls.__name__} must be a dict mapping public "
            f"field names to operator lists, got {fields!r}"
        )
    config = getattr(meta, "config", None)
    if config is None:
        config = FilterConfig()
    elif not isinstance(config, FilterConfig):
        raise ConfigurationError(
            f"`Meta.config` on FilterSet {cls.__name__} must be a FilterConfig, got {config!r}"
        )
    # The FilterSet spellings replace these three config knobs outright;
    # accepting both would create two competing sources of truth.
    for knob, instead in (
        ("operators", "the Meta.fields mapping"),
        ("exclude", "omission from the Meta.fields allow-list"),
        ("sortable", "Meta.sortable"),
    ):
        if getattr(config, knob) not in (None, ()):
            raise ConfigurationError(
                f"FilterConfig.{knob} is not allowed in `Meta.config` of FilterSet "
                f"{cls.__name__}; use {instead} instead"
            )
    sortable = getattr(meta, "sortable", None)
    if sortable is not None:
        if isinstance(sortable, str):
            # A bare string would iterate as characters — same trap as
            # Filterable(ops="eq").
            raise ConfigurationError(
                f"`Meta.sortable` on FilterSet {cls.__name__} takes a sequence of "
                f"field names, got the bare string {sortable!r}"
            )
        sortable = tuple(sortable)
    return model, dict(fields), replace(config, sortable=sortable)


def _allowlist_ops(
    spec: FieldSpec, value: Any, config: FilterConfig, where: str
) -> tuple[str, ...]:
    """Resolve one ``Meta.fields`` entry into validated operator names."""
    if value is OpsMarker.ALL:
        value = ALL_OPS
    if value is OpsMarker.NONE:
        raise ConfigurationError(
            f"field {spec.public_name!r} in {where} maps to ops.NONE; omit the field "
            f"from the allow-list instead — unlisted fields are not filterable"
        )
    if value == ALL_OPS:
        if spec.is_map_value:
            # Value-at-key map parameters are eq-only by design (doc 02).
            return ("eq",)
        names = all_operators_for(spec.py_type, spec.nullable, spec.container)
        if not config.allow_regex:
            # "__all__" mirrors ops.ALL: the regex gate still applies.
            names = tuple(n for n in names if n != "regex")
        if spec.in_element:
            # Collection-level text search never applies inside elements.
            names = tuple(n for n in names if n != "text_search")
        if not names:
            raise ConfigurationError(
                f"field {spec.public_name!r} in {where} maps to '__all__' but type "
                f"{type_name(spec.py_type)} supports no operators; omit the field "
                f"from the allow-list"
            )
        return names
    if isinstance(value, str):
        # A bare string is almost certainly a mistake — "eq" would iterate
        # as 'e', 'q'. Same trap as Filterable(ops="eq").
        raise ConfigurationError(
            f"field {spec.public_name!r} in {where} takes a sequence of operator "
            f"names (or '__all__'), got the bare string {value!r}"
        )
    return _validated_ops(tuple(value), spec, where)


def _claim(seen: dict[str, str], url_name: str, origin: str) -> None:
    """Record one generated parameter name, rejecting collisions."""
    if url_name in seen:
        raise ConfigurationError(
            f"generated parameter name collision on {url_name!r}: "
            f"{origin} conflicts with {seen[url_name]}"
        )
    seen[url_name] = origin


def _declared_filters(cls: type[FilterSet]) -> dict[str, Filter]:
    """Collect ``Filter`` class attributes, base classes first (so a subclass
    overrides — or removes, by assigning a non-Filter — an inherited one)."""
    declared: dict[str, Filter] = {}
    for klass in reversed(cls.__mro__):
        for name, value in vars(klass).items():
            if isinstance(value, Filter):
                declared[name] = value
            else:
                declared.pop(name, None)
    return declared


def _resolve_filterset_params(
    cls: type[FilterSet],
    model: type[BaseModel],
    fields: dict[str, Any],
    specs: tuple[FieldSpec, ...],
    config: FilterConfig,
) -> tuple[ResolvedParam, ...]:
    """Emit the allow-listed and declared parameters, detecting collisions."""
    by_name = {s.public_name: s for s in specs}
    filterable = {s.public_name for s in specs if not _is_unfilterable(s)}
    where = f"{cls.__name__}.Meta.fields"
    params: list[ResolvedParam] = []
    seen: dict[str, str] = {
        name: "(reserved pagination/sort parameter)" for name in RESERVED_PARAMS
    }

    def lookup(public_name: str, context: str) -> FieldSpec:
        spec = by_name.get(public_name)
        if spec is None:
            raise ConfigurationError(
                f"unknown field {public_name!r} in {context} for model "
                f"{model.__name__}; known fields: {', '.join(sorted(filterable))}"
            )
        if _is_unfilterable(spec):
            raise ConfigurationError(
                f"field {public_name!r} of model {model.__name__} is marked "
                f"Filterable(ops=ops.NONE) and cannot be named in {context}; "
                f"remove the marker to make it filterable"
            )
        return spec

    for public_name, value in fields.items():
        spec = lookup(public_name, where)
        for op_name in _allowlist_ops(spec, value, config, where):
            op = DEFAULT_REGISTRY[op_name]
            names = [f"{spec.public_name}{config.separator}{op.name}"]
            if op.name == "eq":
                # Bare equality: `?name=x` is sugar for `name__eq=x`.
                names.append(spec.public_name)
            for url_name in names:
                origin = (
                    f"field {spec.public_name!r} (source {spec.source!r}), operator {op.name!r}"
                )
                _claim(seen, url_name, origin)
                params.append(
                    ResolvedParam(
                        url_name=url_name,
                        python_name=_python_name(url_name),
                        spec=spec,
                        operator=op,
                    )
                )
    for attr_name, declared in _declared_filters(cls).items():
        context = f"{cls.__name__}.{attr_name}"
        spec = lookup(declared.field, context)
        _validated_ops((declared.op,), spec, context)
        url_name = declared.param or attr_name
        _claim(
            seen,
            url_name,
            f"declared filter {context} (field {declared.field!r}, operator {declared.op!r})",
        )
        params.append(
            ResolvedParam(
                url_name=url_name,
                python_name=_python_name(url_name),
                spec=spec,
                operator=DEFAULT_REGISTRY[declared.op],
                description=declared.description,
            )
        )
    return tuple(params)


def _build_filterset_plan(cls: type[FilterSet]) -> QueryPlan:
    """Derive the full query plan for one FilterSet class, at definition time.

    All configuration errors — unknown fields, invalid operators, opted-out
    fields, bad ``Meta``, parameter-name collisions — are raised here, at
    import, never at request time. The plan is a plain
    :class:`~fast_pager.params.QueryPlan`, so request handling (and the
    resulting :class:`~fast_pager.FilterQuery`) is identical to the
    zero-config and ``Filterable`` paths.
    """
    model, fields, config = _parse_meta(cls)
    specs = introspect_model(model, separator=config.separator, max_depth=config.max_depth)
    params = _resolve_filterset_params(cls, model, fields, specs, config)
    # Sortable defaults reuse the standard resolution with "filterable" ==
    # "allow-listed": listed scalar fields sort by default, Filterable
    # absolutes and the Meta.sortable allow-list behave exactly as on routes.
    listed = tuple(s for s in specs if s.public_name in fields)
    sortable = _resolve_sortable(model, specs, listed, config)
    params_model = _build_params_model(f"{cls.__name__}Params", params, sortable, config)
    return QueryPlan(
        model=model,
        config=config,
        params=params,
        params_model=params_model,
        sortable=sortable,
        sources={s.public_name: s.source for s in specs},
        known_params=frozenset(p.url_name for p in params) | frozenset(RESERVED_PARAMS),
    )
