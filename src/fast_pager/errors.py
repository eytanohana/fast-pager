"""Exception types raised by fast-pager.

Configuration problems surface at route-registration time as
:class:`ConfigurationError` so misconfiguration is caught at application
startup, never as a runtime 500.
"""

__all__ = ["CompilationError", "ConfigurationError", "FastPagerError"]


class FastPagerError(Exception):
    """Base class for all errors raised by fast-pager."""


class ConfigurationError(FastPagerError):
    """Invalid filter configuration, raised at route-registration time.

    Examples: an unknown field named in :class:`~fast_pager.FilterConfig`,
    an operator that is not valid for a field's type, or a sort field that
    is not sortable. The message always names the offending field and/or
    operator.
    """


class CompilationError(FastPagerError):
    """A backend compiler was asked to compile an operator it does not support."""
