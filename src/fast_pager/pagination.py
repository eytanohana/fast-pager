"""The optional response envelope: ``Page[T]`` and the collection runner.

``Page[T]`` is a generic Pydantic model, so ``response_model=Page[User]``
and the generated OpenAPI schema stay correct (design doc 01, *Response
envelope*). The runner behind :meth:`~fast_pager.FilterQuery.paginate` is
**duck-typed**: it drives any Mongo-like collection through the standard
surface — ``find(filter)`` returning a cursor with ``sort``/``skip``/
``limit``, plus ``count_documents(filter)`` and
``estimated_document_count()`` — and detects awaitables at runtime, so
motor, pymongo (sync *and* async), and in-memory fakes all work. This
module imports **no database driver**, keeping the core invariant intact.
"""

from __future__ import annotations

import inspect
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel

__all__ = ["Page", "TotalMode", "paginate_collection"]

ItemT = TypeVar("ItemT")

#: The count-cost knob of ``paginate()`` (design doc 01): an exact count runs
#: ``count_documents`` with the same filter on every page request, which is
#: expensive on large collections — so the cost is explicit.
TotalMode = Literal["exact", "estimated", "none"]

_TOTAL_MODES: tuple[str, ...] = ("exact", "estimated", "none")


class Page(BaseModel, Generic[ItemT]):
    """One page of results plus its pagination window — the response envelope.

    Opt-in sugar for the common "list + total count" shape::

        @app.get("/users", response_model=Page[User])
        async def list_users(q: FilterQuery[User] = FilterDepends(User)):
            return await q.paginate(db.users)

    ``total`` is optional because the count is not free: it is ``None``
    whenever the count was skipped (``paginate(..., total="none")``) or when
    an envelope is built by hand without one. ``limit``/``offset`` always
    describe the returned window, whichever pagination strategy (``limit``/
    ``offset`` or ``page``/``page_size``) the request used.
    """

    items: list[ItemT]
    total: Optional[int] = None
    limit: int
    offset: int


def _validate_collection(collection: Any) -> Any:
    """Return the collection's ``find`` callable or raise a clean error."""
    find = getattr(collection, "find", None)
    if not callable(find):
        raise TypeError(
            f"paginate() expects a Mongo-like collection exposing find(filter); "
            f"got {type(collection).__name__!s} without a callable 'find'"
        )
    return find


async def _maybe_await(value: Any) -> Any:
    """Return ``value``, awaiting it first when the driver made it awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _fetch_items(cursor: Any) -> list[Any]:
    """Exhaust a cursor into a list, whatever its (a)sync iteration style.

    Preference order matches the drivers' own idioms: ``to_list`` (motor and
    modern pymongo, sync or async), then async iteration, then plain
    iteration.
    """
    to_list = getattr(cursor, "to_list", None)
    if callable(to_list):
        # motor requires an explicit length argument; None means "all
        # remaining" there and is simply the default for pymongo.
        return list(await _maybe_await(to_list(None)))
    if hasattr(cursor, "__aiter__"):
        return [doc async for doc in cursor]
    if hasattr(cursor, "__iter__"):
        return list(cursor)
    raise TypeError(
        f"paginate() cannot iterate the cursor returned by find(): "
        f"{type(cursor).__name__!s} has no to_list(), __aiter__, or __iter__"
    )


async def _count_total(collection: Any, where: dict[str, Any], total: TotalMode) -> int | None:
    """Resolve the ``total`` count per the requested mode.

    ``"estimated"`` uses ``estimated_document_count()`` — metadata-based and
    cheap, but only meaningful for an *unfiltered* query — so it applies only
    when the compiled filter is empty; a filtered query (or a collection
    without the method) **falls back to an exact count** per the design
    doc's pinned rule. ``"none"`` never touches the collection.
    """
    if total == "none":
        return None
    if total == "estimated" and not where:
        estimate = getattr(collection, "estimated_document_count", None)
        if callable(estimate):
            return int(await _maybe_await(estimate()))
    count = getattr(collection, "count_documents", None)
    if not callable(count):
        raise TypeError(
            f"paginate(total={total!r}) needs the collection to expose "
            f"count_documents(filter); {type(collection).__name__!s} does not "
            f"(use total='none' to skip counting)"
        )
    return int(await _maybe_await(count(where)))


async def paginate_collection(
    collection: Any,
    *,
    where: dict[str, Any],
    order: list[tuple[str, int]],
    limit: int,
    offset: int,
    total: TotalMode = "exact",
) -> Page[Any]:
    """Run one find (+ optional count) against a Mongo-like collection.

    The driver-neutral engine behind :meth:`fast_pager.FilterQuery.paginate`
    — see that method for the user-facing contract. ``order`` is only
    applied when non-empty (drivers reject empty sort specs).
    """
    if total not in _TOTAL_MODES:
        raise ValueError(
            f"total must be one of {', '.join(map(repr, _TOTAL_MODES))}; got {total!r}"
        )
    find = _validate_collection(collection)
    cursor = find(where)
    if order:
        cursor = cursor.sort(order)
    cursor = cursor.skip(offset).limit(limit)
    items = await _fetch_items(cursor)
    count = await _count_total(collection, where, total)
    return Page(items=items, total=count, limit=limit, offset=offset)
