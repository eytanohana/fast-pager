# fast-pager

> Turn your Pydantic models into filterable, sortable, paginated FastAPI query parameters — automatically.

`fast-pager` reads the Pydantic models you already use in your FastAPI routes and
generates **type-safe query parameters** for filtering, sorting and pagination.
Those parameters show up in your OpenAPI docs for free, and compile down to a
real database query (MongoDB first, more backends later).

```python
class User(BaseModel):
    name: str
    age: int

@app.get("/users")
async def list_users(q: FilterQuery[User] = FilterDepends(User)):
    return await db.users.find(q.to_mongo()).to_list(None)
```

A request to:

```
GET /users?name__contains=ana&age__gte=21&age__lt=65&sort=-age&limit=20
```

…compiles to:

```python
{"name": {"$regex": "ana"}, "age": {"$gte": 21, "$lt": 65}}
# sort=[("age", -1)], skip=0, limit=20
# (values in `contains` filters are regex-escaped before compilation)
```

…and every one of those parameters is documented, validated and typed in `/docs`.

---

## Built with AI

This project is designed and developed with the assistance of AI (Anthropic's
Claude Code). Design documents and code are AI-generated and human-reviewed.

---

## Status

`fast-pager` is in the **design phase**. The package is published to PyPI as a
placeholder (`0.0.x`) to reserve the name — it contains no functionality yet.
Don't depend on it until `0.1`.

## Releasing (maintainers)

Releases are fully automated. From a clean `main`:

```bash
./scripts/release.sh patch     # or minor / major
```

The script bumps the version in `pyproject.toml` (via `uv version --bump`),
commits, tags `v<version>`, and pushes. The tag triggers
[`release.yml`](.github/workflows/release.yml), which:

1. verifies the tag matches the `pyproject.toml` version,
2. runs the full CI matrix,
3. builds with `uv build` and publishes to PyPI via **Trusted Publishing**
   (OIDC — no API tokens),
4. creates the GitHub Release with generated notes.

`fast_pager.__version__` reads from package metadata, so the version lives in
exactly one place.

---

## Design documents

This repository currently contains **only the product design** — no implementation yet.
Read the docs in order:

| # | Document | What it covers |
|---|----------|----------------|
| 00 | [Overview & Vision](docs/design/00-overview.md) | The problem, goals, non-goals, naming, guiding principles |
| 01 | [Developer Experience](docs/design/01-developer-experience.md) | API surface options explored, the recommended ergonomics |
| 02 | [Type & Operator System](docs/design/02-type-and-operator-system.md) | Which Python types we support, their operators, compound types, per-field configurability |
| 03 | [Architecture](docs/design/03-architecture.md) | The layered pipeline, the filter AST, the FastAPI signature trick |
| 04 | [Backend Roadmap](docs/design/04-backend-roadmap.md) | Mongo today, generalizing to any database tomorrow |
| 05 | [Roadmap & Release Plan](docs/design/05-roadmap-and-release.md) | Phased path to a clean 1.0 on PyPI |

Start with **[00-overview.md](docs/design/00-overview.md)**.
