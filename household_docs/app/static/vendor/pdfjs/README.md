# PDF.js (vendored)

Mozilla's PDF.js, `pdfjs-dist` **6.2.108**, the *legacy* build (for older phone web views), used by `pdfview.js` to
draw a PDF's pages inside Household Docs. Apache License 2.0 (`LICENSE`).

Copied unchanged from the npm package:

- `pdf.min.mjs`, `pdf.worker.min.mjs` — `legacy/build/`
- `standard_fonts/` — fonts for PDFs that don't embed their own (Foxit and Liberation fonts, licences inside)
- `cmaps/` — character maps for CJK text
- `wasm/openjpeg_nowasm_fallback.js`, `wasm/jbig2_nowasm_fallback.js` — the JavaScript image decoders (JPEG 2000,
  JBIG2), with their licences. The WebAssembly decoders are left out: the app's page policy doesn't allow
  WebAssembly, and the viewer asks for the JavaScript ones (`useWasm: false`).

Left out on purpose: the viewer UI (`web/`), scripting (`pdf.sandbox`, QuickJS), colour profiles (`iccs/`, qcms) and
source maps. To update, replace these files from a newer `pdfjs-dist` and bump the version above.
