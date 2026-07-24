---
icon: lucide/wrench
---

# The docs site

This page documents the documentation site itself: how it's configured, how
to preview and build it locally, how it deploys, and how to add a page.

## What it's built with

The site is built with [Zensical](https://zensical.org), the static site
generator from the creators of Material for MkDocs. It's declared as an
optional dependency group in `pyproject.toml`:

```toml
[dependency-groups]
docs = [
    "zensical>=0.0.51",
]
```

Install it (and everything else) with:

```bash
uv sync --group docs
```

## Layout

```text
zensical.toml            # site configuration (root of the repo)
docs/                     # docs_dir — every page lives under here
├── index.md              # landing page
├── getting-started.md
├── tutorial/
│   ├── filtering.md
│   └── sorting-pagination.md
├── reference/
│   └── operators.md
├── design/                # the product design docs (docs/design/*.md) — do not edit
│   ├── index.md           # section landing page, added for the nav
│   └── 00-overview.md ... 05-roadmap-and-release.md
├── contributing/
│   └── docs-site.md       # this page
└── changelog.md
```

`docs_dir` defaults to `docs/` in Zensical, so it isn't set explicitly in
`zensical.toml` — see
[Basics](https://zensical.org/docs/setup/basics/) for the full list of
defaults.

## `zensical.toml`

The config lives at the repo root (`/zensical.toml`), not inside `docs/`.
The notable choices, each linked to the Zensical doc page that explains it:

- **`nav`** is explicit rather than derived from the directory tree, so
  ordering (Tutorial before Reference, Design as its own section) and page
  titles are deliberate. See
  [Navigation](https://zensical.org/docs/setup/navigation/).
- **`repo_url` / `edit_uri`** point at this repository. Zensical's default
  `edit_uri` assumes a `master` branch; since this repo's default branch is
  `main`, `edit_uri = "edit/main/docs/"` is set explicitly. See
  [Repository](https://zensical.org/docs/setup/repository/).
- **`navigation.indexes`** is on so the `design/index.md` section-landing
  page attaches directly to the "Design" nav section instead of needing a
  separate top-level entry. See
  [Section index pages](https://zensical.org/docs/setup/navigation/#section-index-pages).
- **Markdown extensions** mirror the set Zensical ships by default (via
  `zensical new`), spelled out explicitly in `zensical.toml` so it's visible
  what's enabled without digging into Zensical's defaults. See
  [Extensions](https://zensical.org/docs/setup/extensions/).

## Local preview

```bash
uv run zensical serve
```

Serves the site at `http://localhost:8000` with live rebuild on file
changes. Pass `-o`/`--open` to open a browser automatically, or
`-a`/`--dev-addr` to bind a different address/port.

## Building

```bash
uv run zensical build
```

Builds the static site into `site/` (Zensical's default `site_dir`). This is
exactly what CI runs — see [Deployment](#deployment) below. Pass `-c/--clean`
to force a clean rebuild, or `-s/--strict` to fail the build on warnings
(e.g. broken internal links).

## Adding a page

1. Add a Markdown file under `docs/` (or one of its subdirectories).
2. Add it to the `nav` array in `zensical.toml`, as a `{ "Title" = "path.md" }`
   entry (or nested inside a section's array — see the existing `nav` in
   `zensical.toml` for examples).
3. Run `uv run zensical build` (or `serve`) locally to confirm it renders and
   there are no broken-link warnings.

Adding a new top-level section works the same way: add a new
`{ "Section Name" = [ ... ] }` entry to `nav` with its pages listed inside.

## Deployment

`.github/workflows/docs.yml` builds the site:

- **On pull requests** — `uv run zensical build` runs as a CI check (no
  deploy). This catches broken pages/config before merge.
- **On push to `main`** — the same build runs, then the `site/` output is
  uploaded and deployed to **GitHub Pages** via
  `actions/upload-pages-artifact` + `actions/deploy-pages`, the same flow
  Zensical's own `zensical new` template ships (see the `.github/workflows/`
  directory bundled with the `zensical` package for reference).

The deployed site is served at
**https://eytanohana.github.io/fast-pager/**, which matches the `site_url`
configured in `zensical.toml`.

!!! warning "One manual repo setting required"
    For the deploy job to work, the repository's **Settings → Pages →
    Build and deployment → Source** must be set to **"GitHub Actions"**
    (instead of "Deploy from a branch"). This is a one-time setting change a
    maintainer with repo admin access needs to make; it isn't something a
    workflow file can set.

## Design docs are read-only here

`docs/design/*.md` are copied into the nav as-is and must not be edited from
this site's tooling or content changes — they're the canonical design
documents for the whole project (see `DEVELOPMENT_PLAN.md`). If a design doc
needs to change, change it directly and treat the docs-site content
(Tutorial, Reference, Getting Started) as downstream of it, updating those
pages to match.
