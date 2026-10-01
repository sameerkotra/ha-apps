# Sanscript (vendored)

Used by **Suggest from English** for names in Telugu / Hindi script (§13.7).

- Library: Sanscript 1.3.3, https://github.com/indic-transliteration/sanscript.js
  (commit 24e15105), `src/sanscript.js`, unmodified. MIT.
- Scheme tables: https://github.com/indic-transliteration/common_maps
  (commit c6fc8ca4), converted from TOML to JSON exactly as the library's own
  `scripts/build.js` does, keeping only telugu, devanagari, iso, kolkata_v2,
  itrans, itrans_dravidian, optitrans, optitrans_dravidian, hk and iast. MIT.

Loaded only when someone presses *Suggest from English*; nothing is fetched
from outside the add-on.

SHA-256 (checked by tests/test_names.py):

- sanscript.js c8334b5d9d03dc499fc73a051c489dc4121a8efc75ae5dbcff8aac9797d22694
