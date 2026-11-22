# Third-party Components

## Infinite Canvas

- Project: https://github.com/tigerowo/infinite-canvas
- Runtime version: 0.8.9, from the Windows installer supplied by the project owner.
- Archive: `artifacts/infinite-canvas-0.8.9-web.zip`.
- SHA-256 and provenance: `artifacts/manifest.json`.
- Upstream license: AGPL-3.0; a copy is included in `licenses/Infinite-Canvas-AGPL-3.0.txt`.

This is a snapshot of the compiled web runtime and its standalone dependencies,
not the exact matching upstream TypeScript source. Studio's integration code is
in `work/yingliu-studio/backend/infinite_canvas.py` and `work/yingliu-studio/web/`.
The separate upstream source checkout inspected during development was revision
`b1f10a1ba21b75577f1ddd8743eeee22aade8b3e`; it is not a reproducible source tree for
this installer. Preserve applicable notices and obtain corresponding source
before redistributing the runtime publicly. The bundled Node dependencies retain
the notices shipped with the installer.

## jimeng2api

- Project: https://github.com/rsfree/jimeng2api
- Base revision: `b0cd6afdd109ae4d4e0ee6a6747aeba26dc479a9`.
- Location: `work/research-jimeng2api/`.
- This snapshot includes local changes for account session encryption, Studio
  integration, account issue handling, video capabilities, and concurrency settings.

The checked-out upstream revision does not include a standalone LICENSE file.
This private project backup does not assign it a new license. Existing upstream
documentation and attribution are preserved.

