---
icon: lucide/compass
---

# Design Documents

This section reproduces the product design documents from
[`docs/design/`](https://github.com/eytanohana/fast-pager/tree/main/docs/design)
in the repository, unedited. They're the source of truth the rest of this
site (Getting Started, the Tutorial, the Operator Reference) is written
from — where the two disagree, the design docs win.

Read them in order:

| # | Document | What it covers |
|---|----------|----------------|
| 00 | [Overview & Vision](00-overview.md) | The problem, goals, non-goals, naming, guiding principles |
| 01 | [Developer Experience](01-developer-experience.md) | API surface options explored, the recommended ergonomics |
| 02 | [Type & Operator System](02-type-and-operator-system.md) | Which Python types we support, their operators, compound types, per-field configurability |
| 03 | [Architecture](03-architecture.md) | The layered pipeline, the filter AST, the FastAPI signature trick |
| 04 | [Backend Roadmap](04-backend-roadmap.md) | Mongo today, generalizing to any database tomorrow |
| 05 | [Roadmap & Release Plan](05-roadmap-and-release.md) | Phased path to a clean 1.0 on PyPI |

Start with **[00 — Overview & Vision](00-overview.md)**.

!!! note "This section is generated verbatim from source"
    These pages are the exact Markdown files from `docs/design/*.md` — this
    site does not edit or reformat them. If something here looks stale
    relative to the implementation, the design doc is the thing to fix
    first (per the [development plan](https://github.com/eytanohana/fast-pager/blob/main/DEVELOPMENT_PLAN.md)),
    not this site.
