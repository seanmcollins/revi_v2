# Third-party components

Revi itself is licensed under [PolyForm Noncommercial 1.0.0](LICENSE.md). That license
covers Revi's own source. The components below arrive under their own terms, which
continue to apply to them — including where Revi's noncommercial restriction does not.

Anything vendored (copied into this tree) is called out as such; everything else is a
declared dependency resolved at install time and never redistributed from this
repository. Exact resolved versions live in `uv.lock` and `apps/web/pnpm-lock.yaml`.

## Vendored into this repository

| Component | License | Where | Notes |
|---|---|---|---|
| [shadcn/ui](https://ui.shadcn.com) | MIT | `apps/web/src/components/ui/` | Component source is copied into the tree by design — shadcn/ui distributes code, not a package. Copyright (c) 2023 shadcn. |

## Declared dependencies (not vendored)

| Component | License | Used as |
|---|---|---|
| [Radix UI](https://www.radix-ui.com) (`radix-ui`) | MIT | Frontend primitives underneath the shadcn/ui components. |
| [tw-animate-css](https://github.com/Wombosvideo/tw-animate-css) | MIT | Tailwind animation utilities (dev dependency). |
| [Geist and Geist Mono](https://vercel.com/font) | SIL Open Font License 1.1 | Typefaces, delivered by the `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` packages. Not vendored here; the OFL travels with the font files. |
| [psycopg](https://www.psycopg.org) (psycopg 3) | LGPL-3.0-or-later | Postgres driver, used as a library through its public API. Revi does not modify or statically link it, so the LGPL's reciprocity obligations attach to psycopg itself, not to Revi's source. |

The remaining Python and JavaScript dependencies are permissively licensed
(MIT / BSD / Apache-2.0 / ISC). Resolve the full set from the lockfiles above if you
need a complete manifest.

## Code lists and reference data

X12 claim-adjustment group codes and Claim Adjustment Reason Codes are cited in
`packs/base-rcm/codes.yaml` by code, title, and X12 as maintainer. The official X12
code descriptions are licensed content and are **not** reproduced anywhere in this
repository. Every `definition_paraphrase` in that file is an independent explanation
written for Revi; `packages/pack/tests/test_code_text_independence.py` pins that
property without shipping the official text.

Benchmark values and industry figures under `packs/` and `docs/research/` carry their
sources inline. They are cited, not relicensed.
