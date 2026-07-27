"""Tests for the `Page[T]` envelope and `q.paginate()` (Stage 4).

`paginate()` is duck-typed against the standard Mongo collection surface, so
everything here runs against in-memory fakes — one family per driver style
(pymongo sync, motor/pymongo-async) — with no driver imported anywhere.
Async paths run on the anyio pytest plugin (asyncio backend by default).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from conftest import User
from fast_pager import FilterConfig, FilterQuery, Page
from fast_pager.params import build_plan

DOCS = [{"name": f"user{i}", "age": 20 + i} for i in range(5)]


def make_query(raw: dict, config: FilterConfig | None = None) -> FilterQuery:
    plan = build_plan(User, config or FilterConfig())
    return FilterQuery(plan, plan.params_model.model_validate(raw))


# ---------------------------------------------------------------------------
# Fake collections: sync (pymongo style) and async (motor / pymongo-async
# style), each recording the calls paginate() makes.
# ---------------------------------------------------------------------------


class FakeCursor:
    """Records the sort/skip/limit chain and serves a window of canned docs."""

    def __init__(self, docs):
        self.docs = docs
        self.sorted_with = None
        self.skipped = 0
        self.limited = None

    def sort(self, keys):
        self.sorted_with = keys
        return self

    def skip(self, n):
        self.skipped = n
        return self

    def limit(self, n):
        self.limited = n
        return self

    def _window(self):
        end = None if self.limited is None else self.skipped + self.limited
        return self.docs[self.skipped : end]


class SyncCursor(FakeCursor):
    def __iter__(self):
        return iter(self._window())


class SyncToListCursor(FakeCursor):
    """Modern pymongo: a sync cursor that also offers `to_list()`."""

    def to_list(self, length=None):
        return self._window()


class AsyncToListCursor(FakeCursor):
    """motor / pymongo-async: `to_list()` returns an awaitable."""

    async def to_list(self, length=None):
        return self._window()


class AsyncIterCursor(FakeCursor):
    """An async cursor exposing only async iteration, no `to_list()`."""

    def __aiter__(self):
        async def gen():
            for doc in self._window():
                yield doc

        return gen()


class SyncCollection:
    """pymongo style: plain returns everywhere."""

    cursor_cls = SyncCursor

    def __init__(self, docs=DOCS):
        self.docs = docs
        self.find_filter = None
        self.count_filter = None
        self.estimated_called = False
        self.cursor = None

    def find(self, filter):
        self.find_filter = filter
        self.cursor = self.cursor_cls(self.docs)
        return self.cursor

    def count_documents(self, filter):
        self.count_filter = filter
        return len(self.docs)

    def estimated_document_count(self):
        self.estimated_called = True
        return 1000


class SyncToListCollection(SyncCollection):
    cursor_cls = SyncToListCursor


class AsyncCollection(SyncCollection):
    """motor / pymongo-async style: methods return awaitables."""

    cursor_cls = AsyncToListCursor

    async def count_documents(self, filter):
        self.count_filter = filter
        return len(self.docs)

    async def estimated_document_count(self):
        self.estimated_called = True
        return 1000


class AsyncIterCollection(AsyncCollection):
    cursor_cls = AsyncIterCursor


# ---------------------------------------------------------------------------
# The Page[T] envelope itself.
# ---------------------------------------------------------------------------


def test_page_is_a_generic_pydantic_model():
    page = Page[int](items=[1, 2], total=10, limit=2, offset=0)
    assert page.items == [1, 2]
    assert page.model_dump() == {"items": [1, 2], "total": 10, "limit": 2, "offset": 0}


def test_page_total_is_optional_and_defaults_to_none():
    page = Page[str](items=["a"], limit=1, offset=0)
    assert page.total is None


def test_page_validates_item_types():
    with pytest.raises(ValueError):
        Page[int](items=["not-an-int"], limit=1, offset=0)


def test_public_page_is_the_envelope_and_pagespec_is_the_ast_node():
    # The v0.3.0 name-collision resolution: `fast_pager.Page` is the response
    # envelope; the AST pagination window is `PageSpec` (also at top level).
    import fast_pager
    from fast_pager.ast import PageSpec

    assert fast_pager.Page is Page
    assert issubclass(fast_pager.Page, BaseModel)
    assert fast_pager.PageSpec is PageSpec
    assert not hasattr(fast_pager.ast, "Page")


# ---------------------------------------------------------------------------
# paginate() against every collection style.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "collection_cls",
    [SyncCollection, SyncToListCollection, AsyncCollection, AsyncIterCollection],
)
async def test_paginate_returns_the_window_and_exact_total(collection_cls):
    collection = collection_cls()
    q = make_query({"age__gte": 21, "sort": "-age", "limit": 2, "offset": 1})
    page = await q.paginate(collection)
    assert isinstance(page, Page)
    assert page.items == DOCS[1:3]
    assert page.total == 5 and page.limit == 2 and page.offset == 1
    # The query's compiled parts drove the collection calls.
    assert collection.find_filter == {"age": {"$gte": 21}}
    assert collection.count_filter == {"age": {"$gte": 21}}
    assert collection.cursor.sorted_with == [("age", -1)]
    assert collection.cursor.skipped == 1 and collection.cursor.limited == 2


@pytest.mark.anyio
@pytest.mark.parametrize("collection_cls", [SyncCollection, AsyncCollection])
async def test_paginate_total_none_skips_the_count(collection_cls):
    collection = collection_cls()
    page = await make_query({}).paginate(collection, total="none")
    assert page.total is None
    assert collection.count_filter is None and not collection.estimated_called


@pytest.mark.anyio
@pytest.mark.parametrize("collection_cls", [SyncCollection, AsyncCollection])
async def test_paginate_total_estimated_used_only_for_empty_filters(collection_cls):
    collection = collection_cls()
    page = await make_query({}).paginate(collection, total="estimated")
    assert page.total == 1000
    assert collection.estimated_called and collection.count_filter is None


@pytest.mark.anyio
@pytest.mark.parametrize("collection_cls", [SyncCollection, AsyncCollection])
async def test_paginate_total_estimated_falls_back_to_exact_when_filtered(collection_cls):
    # The design doc 01 pinned rule: estimatedDocumentCount is only valid for
    # unfiltered queries, so a non-empty filter falls back to an exact count.
    collection = collection_cls()
    page = await make_query({"age__gte": 21}).paginate(collection, total="estimated")
    assert page.total == 5
    assert not collection.estimated_called
    assert collection.count_filter == {"age": {"$gte": 21}}


@pytest.mark.anyio
async def test_paginate_estimated_falls_back_when_the_method_is_missing():
    class NoEstimateCollection(SyncCollection):
        estimated_document_count = None  # not callable → exact fallback

    collection = NoEstimateCollection()
    page = await make_query({}).paginate(collection, total="estimated")
    assert page.total == 5
    assert collection.count_filter == {}


@pytest.mark.anyio
async def test_paginate_does_not_sort_when_no_sort_was_requested():
    collection = SyncCollection()
    await make_query({}).paginate(collection)
    assert collection.cursor.sorted_with is None


@pytest.mark.anyio
async def test_paginate_uses_the_page_strategy_window():
    collection = SyncCollection()
    q = make_query({"page": 3, "page_size": 2}, FilterConfig(pagination="page"))
    page = await q.paginate(collection)
    assert page.limit == 2 and page.offset == 4
    assert collection.cursor.skipped == 4 and collection.cursor.limited == 2
    assert page.items == DOCS[4:6]


# ---------------------------------------------------------------------------
# Clean errors for unsupported objects and modes.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_paginate_rejects_objects_without_find():
    with pytest.raises(TypeError, match="find"):
        await make_query({}).paginate(object())


@pytest.mark.anyio
async def test_paginate_rejects_uniterable_cursors():
    class WeirdCursor(FakeCursor):
        pass

    class WeirdCollection(SyncCollection):
        cursor_cls = WeirdCursor

    with pytest.raises(TypeError, match="to_list"):
        await make_query({}).paginate(WeirdCollection())


@pytest.mark.anyio
async def test_paginate_names_count_documents_when_missing():
    class CountlessCollection:
        def find(self, filter):
            return SyncCursor(DOCS)

    with pytest.raises(TypeError, match="count_documents"):
        await make_query({}).paginate(CountlessCollection())
    # total="none" never needs the count surface.
    page = await make_query({}).paginate(CountlessCollection(), total="none")
    assert page.total is None


@pytest.mark.anyio
async def test_paginate_rejects_unknown_total_modes():
    with pytest.raises(ValueError, match="total"):
        await make_query({}).paginate(SyncCollection(), total="guess")


# ---------------------------------------------------------------------------
# End to end: response_model=Page[T] in a real FastAPI app, OpenAPI included.
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    name: str
    age: int


def make_app(collection) -> TestClient:
    from fast_pager import FilterDepends

    app = FastAPI()

    @app.get("/users", response_model=Page[UserOut])
    async def list_users(q: FilterQuery = FilterDepends(User)):
        return await q.paginate(collection)

    return TestClient(app)


def test_endpoint_returns_the_envelope_shape():
    client = make_app(AsyncCollection())
    r = client.get("/users", params={"age__gte": 21, "limit": 2, "offset": 1})
    assert r.status_code == 200
    assert r.json() == {
        "items": [{"name": "user1", "age": 21}, {"name": "user2", "age": 22}],
        "total": 5,
        "limit": 2,
        "offset": 1,
    }


def test_openapi_schema_for_page_of_user_is_correct():
    client = make_app(SyncCollection())
    spec = client.get("/openapi.json").json()
    response = spec["paths"]["/users"]["get"]["responses"]["200"]
    ref = response["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/Page_UserOut_"
    schema = spec["components"]["schemas"]["Page_UserOut_"]
    assert set(schema["required"]) == {"items", "limit", "offset"}
    props = schema["properties"]
    assert props["items"]["type"] == "array"
    assert props["items"]["items"]["$ref"] == "#/components/schemas/UserOut"
    assert props["total"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert props["limit"]["type"] == "integer"
    assert props["offset"]["type"] == "integer"
    assert set(spec["components"]["schemas"]["UserOut"]["properties"]) == {"name", "age"}
