"""The conformance battery run against MongoCompiler, locking its behavior.

The expected-output table below is the Mongo backend's compiled shape for
**every** battery case it supports — the completeness test at the bottom
guarantees a new battery case cannot land without a locked Mongo shape.
"""

import re

import pytest

from fast_pager.backends.mongo import MongoCompiler
from fast_pager.conformance import CASES, UNCHECKED, is_supported, run_battery, run_case

compiler = MongoCompiler()

_TRICKY = re.escape("a.b%_c")  # the battery's escaping probe, regex-escaped


def _len_expr(field, mongo_op, n):
    size = {"$size": {"$cond": [{"$isArray": f"${field}"}, f"${field}", []]}}
    return {"$expr": {mongo_op: [size, n]}}


MONGO_EXPECTED = {
    "scalar-eq": {"age": 21},
    "scalar-ne": {"age": {"$ne": 21}},
    "scalar-gt": {"age": {"$gt": 21}},
    "scalar-gte": {"age": {"$gte": 21}},
    "scalar-lt": {"age": {"$lt": 21}},
    "scalar-lte": {"age": {"$lte": 21}},
    "scalar-in": {"age": {"$in": [1, 2]}},
    "scalar-nin": {"age": {"$nin": [1, 2]}},
    "scalar-between": {"age": {"$gte": 21, "$lte": 65}},
    "string-contains": {"name": {"$regex": _TRICKY}},
    "string-icontains": {"name": {"$regex": _TRICKY, "$options": "i"}},
    "string-startswith": {"name": {"$regex": f"^{_TRICKY}"}},
    "string-istartswith": {"name": {"$regex": f"^{_TRICKY}", "$options": "i"}},
    "string-endswith": {"name": {"$regex": f"{_TRICKY}$"}},
    "string-iendswith": {"name": {"$regex": f"{_TRICKY}$", "$options": "i"}},
    "string-regex": {"name": {"$regex": "^a.*b$"}},
    "string-text-search": {"$text": {"$search": "hello world"}},
    "null-isnull-true": {"nickname": None},
    "null-isnull-false": {"nickname": {"$ne": None}},
    "null-exists-true": {"nickname": {"$exists": True}},
    "null-exists-false": {"nickname": {"$exists": False}},
    "merge-same-field-range": {"age": {"$gte": 21, "$lt": 65}},
    "merge-eq-with-range": {"age": {"$eq": 5, "$lt": 10}},
    "merge-conflicting-string-ops": {
        "$and": [
            {"name": {"$regex": "^a"}, "age": {"$gte": 1}},
            {"name": {"$regex": "b$"}},
        ]
    },
    "group-empty": {},
    "group-or": {"$or": [{"a": 1}, {"b": {"$gt": 2}}]},
    "group-or-nested-in-and": {"$and": [{"a": 1}, {"$or": [{"b": 2}, {"c": 3}]}]},
    "group-and-nested-in-or": {"$or": [{"a": 1}, {"b": 2, "c": 3}]},
    "array-has": {"tags": "python"},
    "array-has-any": {"tags": {"$in": ["a", "b"]}},
    "array-has-all": {"tags": {"$all": ["a", "b"]}},
    "array-len-eq": {"tags": {"$size": 3}},
    "array-len-ne": _len_expr("tags", "$ne", 3),
    "array-len-gt": _len_expr("tags", "$gt", 2),
    "array-len-gte": _len_expr("tags", "$gte", 2),
    "array-len-lt": _len_expr("tags", "$lt", 2),
    "array-len-lte": _len_expr("tags", "$lte", 2),
    "array-empty-true": {"tags": {"$eq": []}},
    "array-empty-false": {"tags.0": {"$exists": True}},
    "array-len-range-pair": {"$and": [_len_expr("tags", "$gte", 2), _len_expr("tags", "$lt", 5)]},
    "array-len-with-membership": {"$and": [{"tags": "x"}, _len_expr("tags", "$gt", 1)]},
    "nested-eq": {"address.city": "ams"},
    "nested-merge-range": {"address.geo.lat": {"$gte": 1.0, "$lt": 2.0}},
    "elem-two-conditions-one-array": {
        "orders": {"$elemMatch": {"amount": {"$gte": 100}, "status": "refunded"}}
    },
    "elem-same-field-merge": {"orders": {"$elemMatch": {"amount": {"$gte": 100, "$lt": 500}}}},
    "elem-two-arrays": {
        "name": "a",
        "orders": {"$elemMatch": {"status": "paid"}},
        "returns": {"$elemMatch": {"status": "open"}},
    },
    "elem-with-shape-condition": {"orders": {"$size": 2, "$elemMatch": {"status": "paid"}}},
    "elem-relative-nested-path": {"orders": {"$elemMatch": {"supplier.name": "acme"}}},
    "elem-nested-hops": {
        "orders": {
            "$elemMatch": {
                "status": "paid",
                "items": {"$elemMatch": {"sku": "x-1", "qty": {"$gte": 2}}},
            }
        }
    },
    "elem-lone-in-or": {"$or": [{"orders": {"$elemMatch": {"status": "paid"}}}, {"a": 1}]},
    "map-has-key": {"metadata.region": {"$exists": True}},
    "map-has-key-in-elem": {"orders": {"$elemMatch": {"meta.gift": {"$exists": True}}}},
    "order-two-keys": [("age", -1), ("name", 1)],
    "order-empty": [],
    "page-window": {"skip": 30, "limit": 10},
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_mongo_conformance(case):
    run_case(compiler, case, MONGO_EXPECTED.get(case.id, UNCHECKED))


def test_expected_table_covers_every_supported_case():
    """Every battery case Mongo supports has a locked expected shape."""
    needed = {case.id for case in CASES if not case.invalid and is_supported(compiler, case)}
    assert needed == set(MONGO_EXPECTED)


def test_run_battery_convenience_passes():
    run_battery(compiler, MONGO_EXPECTED)
