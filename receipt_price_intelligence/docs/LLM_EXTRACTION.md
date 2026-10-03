# Receipt LLM Extraction

## Model configuration

The application receives:

```text
MODEL_URL
MODEL_NAME
MODEL_API_FORMAT
MODEL_API_KEY
MODEL_TIMEOUT_SECONDS
MODEL_TEMPERATURE
```

The model name must be passed exactly as configured.

## Prompt and schema

The prompt, JSON schema, and response handling live in `app/services/receipt_llm.py`
(`PROMPT_VERSION` / `SCHEMA_VERSION` are stored on every draft). The prompt is a
transcription contract: extract only what is printed, copy descriptions verbatim,
use `null` when unsure, never calculate. It also defines the cases small models get wrong:

- quantity vs. weight, and the `2.45 LB @ 1.29/LB   3.16` worked example
- multi-line items (description on one line, math on the next)
- discounts attach to the item above them (`line_subtotal`, positive `discount_amount`,
  `line_total` after discount); order-level discounts go in `discount_total`
- physical store address only, never corporate/HQ addresses
- tax lines as printed (rate only if printed); `grand_total` is the amount charged, not tendered
- several images are consecutive photos of one receipt

The request also carries the schema for structured output (Ollama `format`, OpenAI
`response_format: json_schema`). If a server rejects an optional field (schema, `think`),
the call is retried without it.

## Units

`weight_unit` and `unit_price_unit` are one canonical set: `LB, KG, OZ, G` (weight), `GAL, QT, PT,
L, ML, FLOZ` (volume), `CT` (count), and `"each"` for a price per whole item. The prompt states this
list explicitly and asks for the code, not the printed word ("GALLON" → "GAL"). Whatever the model
answers still goes through `app/services/units.py`, the single table every part of the app uses to
turn a spelling into its code — "GALLON", "Gal.", "GA" and "gallon" all become `GAL`. `app/services/
analytics.py` calls the same function, so a price recorded as `GAL` on one receipt and `GALLON` on
another (from before this table existed) is still compared as the same unit. The review screen's
weight-unit and price-per-unit dropdowns list this same set (`app/services/units.py`'s
`CANONICAL_UNITS`), so every item offers identical choices; a value that predates this and isn't one
of them is still shown, appended to the list, rather than hidden.

## Purchase dates

The model returns the date twice: `purchase_date_text` (exactly as printed, without the time) and
`purchase_date` (its own YYYY-MM-DD conversion). The app parses the printed text itself
(`resolve_purchase_date` in `receipt_llm.py`) and only falls back to the model's conversion if the text
cannot be parsed. It understands year-first and day-first numeric dates, month names ("14 MAR 2025",
"Mar 14, 2025"), two-digit years, weekday names, times, and ISO timestamps. The prompt tells the model to use the
transaction date and ignore return-by, expiry, coupon and promotion dates, and states today's date so
future dates are not invented.

A date that could be month-first or day-first (03/04/25) is decided by the `RECEIPT_DATE_ORDER` setting: `MDY` or `DMY`
always reads it that way; `auto` (default) uses the receipt's currency (USD and CAD month first, other
currencies day first) and, with no currency, the model's own reading. A reading that would be in the future loses to
one that is not. In `auto` mode the extraction records `purchase_date_ambiguous` and `purchase_date_alternatives`, and
the review screen shows the printed text and offers the other reading. Missing, future, old (over two years) and
unresolvable dates produce warnings.

## Correction notes

If the reader got something wrong, the reviewer can add a note on the review screen ("Read again
with a note", up to 1000 characters) and the receipt is read again. The note is **appended to the
standard user prompt** in a delimited block (`build_user_prompt(image_count, correction)`); the system
prompt and schema are unchanged, and the block reminds the model to keep transcribing only what is
printed. The note is cleaned (control characters removed, length capped), stored in `meta.correction`,
and pre-filled the next time so it can be added to. A re-read replaces the items, totals and any edits,
so the screen says so first. If it fails, the earlier result is kept.

## Extraction object (schema 2.0)

```json
{
  "store_name": null, "store_address": null, "store_number": null,
  "purchase_date": null, "purchase_time": null, "receipt_number": null, "currency": null,
  "items": [{
    "receipt_description": "", "item_type": "UNIT|WEIGHT|QUANTITY|UNKNOWN",
    "quantity": null, "weight_value": null, "weight_unit": null,
    "unit_price": null, "unit_price_unit": null,
    "line_subtotal": null, "discount_amount": null, "line_total": null,
    "tax_amount": null, "extraction_confidence": 0.0
  }],
  "taxes": [{"tax_name": null, "tax_rate": null, "taxable_amount": null, "tax_amount": null}],
  "subtotal": null, "discount_total": null, "tax_total": null, "fee_total": null, "grand_total": null,
  "store_confidence": 0.0, "total_confidence": 0.0
}
```

After normalization the stored JSON also contains `validation_warnings`, `meta`
(model, versions, image count, duration) and, once the user edits the header,
`user_edited_fields`.

## Model trust boundary

Model output is untrusted input.

Pipeline (`receipt_llm.py`):

```text
model text
 -> parse_model_json        (strips <think>, fences, prose; rejects API envelopes)
 -> normalize_extraction    (numbers, dates, units, item types, confidence)
 -> validate_extraction     (line math, items vs subtotal, subtotal + tax + fees vs total)
 -> user review             (warnings never block; the review screen shows them)
```

No model result bypasses this pipeline.

## OpenAI-compatible request

The adapter should conceptually send:

```json
{
  "model": "<configured MODEL_NAME>",
  "temperature": 0,
  "messages": [
    {
      "role": "system",
      "content": "..."
    },
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Extract this receipt."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      ]
    }
  ]
}
```

Exact multimodal content format may be adapted to the configured provider, but the internal extraction schema remains unchanged.

## Ollama adapter

Use the configured `MODEL_URL` and send the receipt images plus prompt using the Ollama chat API.
The adapter must normalize the response into the same internal schema.

## No common-name generation

The extraction model should not be responsible for the final common item name.
Normalization may suggest a match separately, but it remains a draft suggestion until user approval.
