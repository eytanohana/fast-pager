"""fast-pager — filterable, sortable, paginated query parameters for FastAPI.

Derives type-safe filter/sort/pagination query parameters from the Pydantic
models you already use, surfaces them in OpenAPI, and compiles them to a
backend query (MongoDB first).

Quick start::

    from fast_pager import FilterDepends, FilterQuery

    @app.get("/users")
    async def list_users(q: FilterQuery[User] = FilterDepends(User)):
        return await db.users.find(q.to_mongo()).sort(q.sort_mongo() or None) \\
            .skip(q.skip).limit(q.limit).to_list(None)

Design documents: https://github.com/eytanohana/fast-pager
"""

from importlib.metadata import version

from .ast import Condition, FilterAST, Group, Page, Sort, SortDirection
from .backends.base import QueryCompiler
from .backends.mongo import MongoCompiler
from .config import FilterConfig
from .dependency import FilterDepends
from .errors import CompilationError, ConfigurationError, FastPagerError
from .filterable import Filterable, OpsMarker, ops
from .filterset import Filter, FilterSet
from .introspection import FieldSpec, introspect_model
from .operators import DEFAULT_REGISTRY, Arity, Container, Operator, Tier, ValueTypeRule
from .query import FilterQuery

__all__ = [
    "DEFAULT_REGISTRY",
    "Arity",
    "CompilationError",
    "Condition",
    "ConfigurationError",
    "Container",
    "FastPagerError",
    "FieldSpec",
    "Filter",
    "FilterAST",
    "FilterConfig",
    "FilterDepends",
    "FilterQuery",
    "FilterSet",
    "Filterable",
    "Group",
    "MongoCompiler",
    "Operator",
    "OpsMarker",
    "Page",
    "QueryCompiler",
    "Sort",
    "SortDirection",
    "Tier",
    "ValueTypeRule",
    "__version__",
    "introspect_model",
    "ops",
]

__version__ = version("fast-pager")
