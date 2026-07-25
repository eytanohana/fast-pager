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
    "mike>=2.2.0",
    "zensical>=0.0.51",
]
```

`mike` powers the versioned deploys — see [Versioning](#versioning) below
for why the `mike` entry above isn't the whole story (the PyPI release
listed here gets overridden with a Zensical-compatible fork before it's
actually used).

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

## Versioning

The docs are versioned, the same way
[py-ds-academy](https://github.com/eytanohana/py-ds-academy)'s docs are:
every release gets its own URL, `latest` always points at the newest
release, and a rolling `dev` version tracks `main`. This is implemented with
[`mike`](https://github.com/jimporter/mike), the same tool
[mkdocs-material](https://squidfunk.github.io/mkdocs-material/setup/setting-up-versioning/)
projects use, deploying to the standard `gh-pages` branch layout.

### Why a fork of `mike` is required

Plain `mike` (the package on PyPI) only knows how to build docs by calling
`mkdocs` directly — it imports `mkdocs.config`, injects an `mkdocs` plugin,
and shells out to `mkdocs build`. It has no idea `zensical.toml` or the
`zensical` CLI exist, so it can't drive this site's build.

Zensical's own team maintains a compatible fork —
[squidfunk/mike](https://github.com/squidfunk/mike) — that replaces the
`mkdocs`-specific internals with calls to `zensical build` instead. Per its
README, **this fork is deliberately not published to PyPI** and must be
installed from git:

```bash
pip install git+https://github.com/squidfunk/mike.git
```

This is an explicitly temporary arrangement: Zensical's
[roadmap](https://zensical.org/about/roadmap/#versioning) lists native
versioning support as coming "in the coming months," at which point this
fork (and the workaround below) should be retired in favor of whatever
Zensical ships natively. See
[zensical.org/docs/setup/versioning/](https://zensical.org/docs/setup/versioning/)
for the upstream setup docs this project follows.

Because the fork can't be resolved as a normal PyPI dependency, `mike` is
declared in the `docs` dependency group in `pyproject.toml` — pinned to a
released `mike` version for the sake of having *some* resolvable metadata —
but every place that actually **runs** `mike` (CI and local use) overrides it
immediately afterward with the git-installed fork:

```bash
uv sync --group docs
uv pip install --python .venv \
  "mike @ git+https://github.com/squidfunk/mike.git@<pinned-commit>"
```

The pinned commit lives in `MIKE_ZENSICAL_REF` in both `docs.yml` and
`release.yml`. Bump it deliberately (and re-test) rather than tracking a
branch, so CI doesn't silently pick up upstream changes.

### `zensical.toml` version config

```toml
[project.extra.version]
provider = "mike"
default = "latest"
alias = true
```

This is what turns on the version-selector dropdown in the header (Zensical
renders it via the same client-side `versions.json`-fetching mechanism
mkdocs-material uses) and tells the theme that `latest` is the
non-"outdated" alias, so older versions get an "you're viewing an outdated
version" banner and `latest` doesn't.

### URL scheme

- `https://eytanohana.github.io/fast-pager/` — redirects to whatever `mike
  set-default` last pointed at (`latest`).
- `https://eytanohana.github.io/fast-pager/latest/` — alias for the newest
  tagged release.
- `https://eytanohana.github.io/fast-pager/X.Y.Z/` — one directory per
  released version (e.g. `/0.1.0/`).
- `https://eytanohana.github.io/fast-pager/dev/` — tracks `main`, rebuilt on
  every push. Useful for previewing unreleased docs changes, but not linked
  from the version selector's "latest" alias and not the default redirect
  target.

### How a version gets published

- **Push to `main`** (`.github/workflows/docs.yml`, `deploy-dev` job) — runs
  `uv run mike deploy --push --branch gh-pages dev`, updating the `dev`
  version in place. This does not touch `latest` or the root redirect.
- **Tag push matching `v*.*.*`** (`.github/workflows/release.yml`,
  `deploy-docs` job) — after the tag-vs-`pyproject.toml` version check and
  CI both pass, runs:

  ```bash
  VERSION="${GITHUB_REF_NAME#v}"   # v0.5.4 -> 0.5.4
  uv run mike deploy --push --branch gh-pages --update-aliases "$VERSION" latest
  uv run mike set-default --push --branch gh-pages latest
  ```

  This deploys the new version, re-points the `latest` alias at it, and
  updates the root redirect to `latest`. **The first versioned docs deploy
  happens automatically the next time a `v*.*.*` tag is pushed** — nothing
  else needs to happen for `X.Y.Z`/`latest` to appear for the first time.

Both jobs authenticate as `github-actions[bot]` (configured via `git config`
before the `mike deploy`/`set-default` calls) and push using the workflow's
own `GITHUB_TOKEN`, so no extra secrets are needed.

### Local preview of the versioned site

```bash
uv sync --group docs
uv pip install --python .venv \
  "mike @ git+https://github.com/squidfunk/mike.git@<commit from docs.yml's MIKE_ZENSICAL_REF>"
uv run mike deploy 0.0.1-local   # or any version name — doesn't push anywhere
uv run mike serve
```

`mike serve` serves the full versioned `gh-pages` layout (with the selector)
from your local `gh-pages` branch at `http://localhost:8000`. This writes
real commits to a local `gh-pages` branch — delete it afterward
(`git branch -D gh-pages`) if you don't want it lying around, and never push
it. For everyday single-version editing, `uv run zensical serve` (see
[Local preview](#local-preview) above) is faster and doesn't touch git at
all.

## Version selector: current limitation

The version selector dropdown **is supported** by Zensical 0.0.51 via the
`provider = "mike"` config above — it is not a documented limitation. What
*is* still rough, and worth flagging for future maintainers:

- The `mike` fork required to make any of this work is unreleased-to-PyPI
  and pinned by commit SHA rather than version tag (squidfunk/mike doesn't
  cut version-tagged releases the way jimporter/mike does). Track
  [Zensical's versioning roadmap item](https://zensical.org/about/roadmap/#versioning)
  for when native support lands and this workaround can be dropped in favor
  of whatever config Zensical ships directly.

## Deployment

`.github/workflows/docs.yml` builds the site:

- **On pull requests** — `uv run zensical build --clean --strict` runs as a
  CI check (no deploy). This catches broken pages/config before merge.
- **On push to `main`** — the same build runs, then (see
  [Versioning](#versioning) above) the `deploy-dev` job publishes the `dev`
  version to the `gh-pages` branch.
- **On a `v*.*.*` tag push** — `.github/workflows/release.yml`'s
  `deploy-docs` job publishes the tagged version and re-points `latest` and
  the root redirect at it.

The deployed site is served at
**https://eytanohana.github.io/fast-pager/**, which matches the `site_url`
configured in `zensical.toml`.

!!! warning "One manual repo setting required"
    Versioned docs deploy via commits to the **`gh-pages`** branch (the
    standard `mike` layout), not via the `actions/deploy-pages` artifact
    flow this repo used previously. For GitHub Pages to actually serve that
    branch, a maintainer with repo admin access must flip **Settings →
    Pages → Build and deployment → Source** from **"GitHub Actions"** to
    **"Deploy from a branch"**, with branch `gh-pages` / folder `/ (root)`.
    This is a one-time change; nothing in this repo's workflows can do it
    for you, and until it's flipped, pushes to `gh-pages` will update the
    branch but the live site won't reflect them.

## Design docs are read-only here

`docs/design/*.md` are copied into the nav as-is and must not be edited from
this site's tooling or content changes — they're the canonical design
documents for the whole project (see `DEVELOPMENT_PLAN.md`). If a design doc
needs to change, change it directly and treat the docs-site content
(Tutorial, Reference, Getting Started) as downstream of it, updating those
pages to match.
