"""MongoDB query compiler.

Compiles the neutral AST into **plain dicts** usable with pymongo and motor
alike — this module imports no database driver. Safety guarantees:

- ``contains``/``startswith``/``endswith`` (and ``i*`` variants) values are
  always ``re.escape()``-d, so user input is a literal substring, never a
  pattern. ``startswith`` is anchored with ``^``, ``endswith`` with ``$``.
- Conditions on the same field merge into one sub-document
  (``{"age": {"$gte": 21, "$lt": 65}}``); genuinely conflicting clauses
  (e.g. two regex conditions on one field) fall back to ``$and``.
- Array length comparisons (``len__ne``/``gt``/``gte``/``lt``/``lte``)
  compile to ``$expr`` over ``$size``, guarded with ``$isArray`` so a
  missing, null, or non-array value is treated as length 0 instead of a
  query execution error. ``$expr`` clauses never merge — each stands alone
  (``$expr`` takes a single expression).
"""

from __future__ import annotations

import enum
import re
from typing import Any

from ..ast import Condition, Group, Page, Sort
from ..errors import CompilationError

__all__ = ["MongoCompiler"]


def _norm(value: Any) -> Any:
    """Normalize AST values into Mongo-encodable plain values."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


class MongoCompiler:
    """Compiles :class:`~fast_pager.ast.FilterAST` parts to plain Mongo dicts."""

    name = "mongo"

    supported_ops: frozenset[str] = frozenset(
        {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "in",
            "nin",
            "between",
            "contains",
            "icontains",
            "startswith",
            "istartswith",
            "endswith",
            "iendswith",
            "regex",
            "text_search",
            "isnull",
            "exists",
            "has",
            "has_any",
            "has_all",
            "len__eq",
            "len__ne",
            "len__gt",
            "len__gte",
            "len__lt",
            "len__lte",
            "empty",
        }
    )

    def compile_where(self, group: Group) -> dict[str, Any]:
        """Compile a filter group into a Mongo query dict.

        AND groups merge same-field conditions into one sub-document; OR
        groups compile to ``$or``. Nested groups recurse.
        """
        if group.op == "or":
            return {"$or": [self._compile_member(m) for m in group.members]}
        merged: dict[str, dict[str, Any]] = {}
        extras: list[dict[str, Any]] = []
        for member in group.members:
            if isinstance(member, Group):
                extras.append(self.compile_where(member))
                continue
            key, fragment = self._fragment(member)
            if key == "$expr":
                # $expr takes a single expression — two $expr fragments must
                # never merge into one sub-document.
                extras.append({key: fragment})
                continue
            bucket = merged.setdefault(key, {})
            if any(k in bucket for k in fragment):
                # Same Mongo operator twice on one field cannot share a
                # sub-document; keep both semantics via $and.
                extras.append({key: self._finalize(fragment)})
            else:
                bucket.update(fragment)
        result = {field: self._finalize(ops) for field, ops in merged.items() if ops}
        if extras:
            clauses = ([result] if result else []) + extras
            return clauses[0] if len(clauses) == 1 else {"$and": clauses}
        return result

    def compile_order(self, order: list[Sort]) -> list[tuple[str, int]]:
        """Compile sort keys to ``[(field, 1|-1), ...]`` for ``sort()``."""
        return [(sort.field, sort.direction.value) for sort in order]

    def compile_page(self, page: Page) -> dict[str, int]:
        """Compile the pagination window to ``{"skip": ..., "limit": ...}``."""
        return {"skip": page.offset, "limit": page.limit}

    def _compile_member(self, member: Condition | Group) -> dict[str, Any]:
        if isinstance(member, Group):
            return self.compile_where(member)
        key, fragment = self._fragment(member)
        return {key: self._finalize(fragment)}

    @staticmethod
    def _finalize(fragment: dict[str, Any]) -> Any:
        # A lone $eq simplifies to the bare value: {"age": {"$eq": 5}} → {"age": 5}.
        # A list value stays explicit — `empty=true` is pinned to the exact
        # form {field: {"$eq": []}} (design doc 02).
        if set(fragment) == {"$eq"} and not isinstance(fragment["$eq"], list):
            return fragment["$eq"]
        return fragment

    def _fragment(self, cond: Condition) -> tuple[str, dict[str, Any]]:
        """Compile one condition to ``(top-level key, operator sub-document)``."""
        op = cond.op
        value = _norm(cond.value)
        field = cond.field
        if op == "eq":
            return field, {"$eq": value}
        if op == "ne":
            return field, {"$ne": value}
        if op in ("gt", "gte", "lt", "lte"):
            return field, {f"${op}": value}
        if op == "in":
            return field, {"$in": list(value)}
        if op == "nin":
            return field, {"$nin": list(value)}
        if op == "between":
            low, high = value
            return field, {"$gte": low, "$lte": high}
        if op == "has":
            # Mongo matches a scalar against array elements natively.
            return field, {"$eq": value}
        if op == "has_any":
            return field, {"$in": list(value)}
        if op == "has_all":
            return field, {"$all": list(value)}
        if op == "len__eq":
            return field, {"$size": value}
        if op in ("len__ne", "len__gt", "len__gte", "len__lt", "len__lte"):
            # Aggregation $size errors on non-arrays; the $isArray guard makes
            # a missing, null, or non-array value count as length 0 instead
            # of failing the whole query at execution time.
            length = {"$size": {"$cond": [{"$isArray": f"${field}"}, f"${field}", []]}}
            return "$expr", {f"${op.removeprefix('len__')}": [length, value]}
        if op == "empty":
            # Pinned semantics (design doc 02): `true` matches the empty
            # array, `false` matches at least one element; a missing field
            # matches neither (use isnull/exists to reason about presence).
            if value:
                return field, {"$eq": []}
            return f"{field}.0", {"$exists": True}
        if op in ("contains", "icontains"):
            fragment = {"$regex": re.escape(str(value))}
        elif op in ("startswith", "istartswith"):
            fragment = {"$regex": "^" + re.escape(str(value))}
        elif op in ("endswith", "iendswith"):
            fragment = {"$regex": re.escape(str(value)) + "$"}
        elif op == "regex":
            # The explicit, gated pattern operator: the value IS the pattern.
            return field, {"$regex": str(value)}
        elif op == "text_search":
            # Collection-level text query over the text index.
            return "$text", {"$search": str(value)}
        elif op == "isnull":
            return field, ({"$eq": None} if value else {"$ne": None})
        elif op == "exists":
            return field, {"$exists": bool(value)}
        else:
            raise CompilationError(
                f"MongoCompiler does not support operator {op!r} (field {field!r})"
            )
        if op.startswith("i"):
            fragment["$options"] = "i"
        return field, fragment
