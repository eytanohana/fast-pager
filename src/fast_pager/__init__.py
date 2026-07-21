"""fast-pager — filterable, sortable, paginated query parameters for FastAPI.

Derives type-safe filter/sort/pagination query parameters from the Pydantic
models you already use, surfaces them in OpenAPI, and compiles them to a
backend query (MongoDB first).

This is a placeholder release reserving the package name while the library is
under active design. See the design documents in the repository:
https://github.com/eytanohana/fast-pager
"""

from importlib.metadata import version

__version__ = version("fast-pager")
