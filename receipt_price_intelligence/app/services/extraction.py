"""Receipt AI extraction service.

Talks to the configured vision model, then hands the reply to ``receipt_llm``
for parsing, normalization, and arithmetic checks. Model output is untrusted:
nothing reaches the database until it has been through that pipeline, and even
then it only becomes a *draft* for the user to review.
"""

import asyncio
import base64
import io
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator

import httpx
from PIL import Image, ImageOps

from app.config import get_settings
from app.db.models import ReceiptDraft, ReceiptDraftItem, ReceiptDraftTax
from app.logging_config import get_logger
from app.services import receipt_llm as llm
from app.services import fill_in
from app.services import corrections, receipt_records

logger = get_logger("extraction")

# Longest image side sent to the model. Phone photos are 4000+ px; that only adds
# tokens and latency. 2048 keeps thermal-receipt text legible.
MAX_IMAGE_SIDE = 2048
JPEG_QUALITY = 88
MAX_IMAGES_PER_RECEIPT = 8

FAILED_STATUS = "EXTRACTION_FAILED"


class ExtractionProgress(str, Enum):
    """Extraction progress stages."""
    STARTING = "STARTING"
    READING_IMAGE = "READING_IMAGE"
    CALLING_MODEL = "CALLING_MODEL"
    PROCESSING_RESPONSE = "PROCESSING_RESPONSE"
    VALIDATING = "VALIDATING"
    SAVING = "SAVING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


@dataclass
class ExtractionEvent:
    """Event emitted during extraction."""
    stage: ExtractionProgress
    message: str
    data: dict | None = None
    error: str | None = None
    # Server-side only (never sent over SSE): the normalized extraction on COMPLETE,
    # or {"raw": <model text>} on ERROR so the failure can be diagnosed.
    result: dict | None = None


NOT_SET_UP = "No model is set up to read receipts yet. An administrator can set it in Admin → App settings."


class ExtractionError(Exception):
    """A failure with a message that is safe and useful to show the user."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


# Backwards-compatible aliases for the prompt constants (now defined in receipt_llm).
EXTRACTION_SYSTEM_PROMPT = llm.SYSTEM_PROMPT


class ExtractionService:
    """Service for extracting receipt data using AI models."""

    def __init__(self):
        settings = self.settings
        self.key = (settings.model_url, settings.model_timeout_seconds)
        # Large vision models can take minutes per receipt on modest hardware.
        self.timeout_seconds = max(settings.model_timeout_seconds, 300)
        self.client = httpx.AsyncClient(
            timeout=self.timeout_seconds,
            base_url=settings.model_url.rstrip("/"),
            headers={"Content-Type": "application/json"},
        )

    @property
    def settings(self):
        """The App settings as they are now (they can change while the app runs)."""
        return get_settings()

    # ------------------------------------------------------------------ #
    # Images
    # ------------------------------------------------------------------ #

    @staticmethod
    def prepare_image_base64(image_path: str) -> str:
        """Load an image, fix its orientation, downscale it, and return base64 JPEG.

        Phone photos usually carry an EXIF rotation flag instead of rotated pixels.
        A model that never sees that flag reads the receipt sideways.
        """
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            longest = max(image.size)
            if longest > MAX_IMAGE_SIDE:
                scale = MAX_IMAGE_SIDE / longest
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.LANCZOS,
                )

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)

        return base64.b64encode(buffer.getvalue()).decode("ascii")

    # ------------------------------------------------------------------ #
    # Model calls
    # ------------------------------------------------------------------ #

    def _effective_format(self) -> str:
        """Ollama's native API is used when configured, or when the URL is the Ollama port."""
        if self.settings.model_api_format.lower() == "ollama" or "11434" in self.settings.model_url:
            return "ollama"
        return "openai_compatible"

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        optional_keys: tuple[str, ...] = (),
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST a request. If the server rejects an optional feature, drop it and retry.

        ``optional_keys`` are request fields (structured output, thinking) that some
        servers or models do not support and answer with HTTP 400.
        """
        request = dict(payload)

        for _ in range(len(optional_keys) + 1):
            response = await self.client.post(path, json=request, headers=headers)
            if response.status_code == 200:
                return response.json()

            detail = response.text[:500]
            if response.status_code in (400, 422):
                mentioned = [k for k in optional_keys if k in request and k in detail]
                to_drop = mentioned or [k for k in optional_keys if k in request]
                if to_drop:
                    logger.warning(
                        "Model server rejected %s (%s); retrying without %s",
                        path, detail[:200], ", ".join(to_drop),
                    )
                    for key in to_drop:
                        request.pop(key)
                    continue

            logger.error("Model API error: %d - %s", response.status_code, detail)
            raise ExtractionError(f"The model server returned HTTP {response.status_code}: {detail[:200]}")

        raise ExtractionError("The model server rejected the request")

    async def _call_model(self, images_b64: list[str], correction: str | None = None) -> tuple[str, bool]:
        """Send the receipt image(s) to the model. Returns (reply text, was_truncated)."""
        if not self.settings.model_ready:
            raise ExtractionError(NOT_SET_UP)
        api_format = self._effective_format()
        logger.info(
            "Calling model %s via %s at %s with %d image(s)",
            self.settings.model_name, api_format, self.settings.model_url, len(images_b64),
        )

        if api_format == "ollama":
            body = await self._call_ollama(images_b64, correction)
        else:
            body = await self._call_openai_compatible(images_b64, correction)

        try:
            return llm.extract_message_text(api_format, body)
        except llm.ReceiptParseError as e:
            raise ExtractionError(str(e), raw=json.dumps(body)[:4000]) from e

    async def _call_openai_compatible(self, images_b64: list[str], correction: str | None = None) -> dict[str, Any]:
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": llm.build_user_prompt(len(images_b64), correction)}
        ]
        for image in images_b64:
            user_content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}}
            )

        payload = {
            "model": self.settings.model_name,
            "temperature": self.settings.model_temperature,
            "max_tokens": self.settings.model_max_output_tokens,
            "messages": [
                {"role": "system", "content": llm.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "receipt", "strict": True, "schema": llm.RECEIPT_JSON_SCHEMA},
            },
        }

        headers = {}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"

        return await self._post_json(
            "/chat/completions", payload, optional_keys=("response_format",), headers=headers
        )

    async def _call_ollama(self, images_b64: list[str], correction: str | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": self.settings.model_temperature,
            "num_predict": self.settings.model_max_output_tokens,
        }
        if self.settings.model_num_ctx > 0:
            options["num_ctx"] = self.settings.model_num_ctx

        payload = {
            "model": self.settings.model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": llm.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": llm.build_user_prompt(len(images_b64), correction),
                    "images": images_b64,
                },
            ],
            # Constrains decoding to the receipt schema when the model supports it.
            "format": llm.RECEIPT_JSON_SCHEMA,
            "think": self.settings.model_think,
            "options": options,
        }

        headers = {}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"

        return await self._post_json("/api/chat", payload, optional_keys=("think", "format"), headers=headers)

    # ------------------------------------------------------------------ #
    # Extraction pipeline
    # ------------------------------------------------------------------ #

    async def extract_receipt(
        self,
        draft: ReceiptDraft,
        image_paths: list[str],
        correction: str | None = None,
    ) -> AsyncGenerator[ExtractionEvent, None]:
        """Extract receipt data with streaming progress.

        Yields progress events. The final event is COMPLETE (``result`` holds the
        normalized extraction) or ERROR (``error`` holds a user-facing message).
        Persisting the result is the caller's job; see ``save_extraction_to_draft``.
        """
        started = time.monotonic()
        raw_text: str | None = None
        correction = llm.clean_correction(correction)

        try:
            yield ExtractionEvent(
                stage=ExtractionProgress.STARTING,
                message="Starting extraction",
                data={"draft_id": draft.id},
            )

            if not image_paths:
                raise ExtractionError("This draft has no images to read")
            image_paths = image_paths[:MAX_IMAGES_PER_RECEIPT]

            yield ExtractionEvent(
                stage=ExtractionProgress.READING_IMAGE,
                message=f"Preparing {len(image_paths)} image(s)",
            )
            try:
                images = [
                    await asyncio.to_thread(self.prepare_image_base64, path) for path in image_paths
                ]
            except (OSError, ValueError) as e:
                raise ExtractionError(f"Could not read the receipt image: {e}") from e

            parsed: dict[str, Any] | None = None
            for attempt in (1, 2):
                yield ExtractionEvent(
                    stage=ExtractionProgress.CALLING_MODEL,
                    message=(
                        f"Reading receipt with {self.settings.model_name}"
                        if attempt == 1
                        else "Model reply was not valid JSON; trying once more"
                    ),
                    data={"model": self.settings.model_name, "attempt": attempt},
                )

                raw_text, truncated = await self._call_model(images, correction)

                yield ExtractionEvent(
                    stage=ExtractionProgress.PROCESSING_RESPONSE,
                    message="Processing model response",
                )
                try:
                    parsed = llm.parse_model_json(raw_text)
                    break
                except llm.ReceiptParseError as e:
                    logger.warning("Attempt %d: unusable model output: %s | %.300s", attempt, e, raw_text)
                    if truncated:
                        raise ExtractionError(
                            "The model's reply was cut off before it finished. "
                            "Increase MODEL_MAX_OUTPUT_TOKENS or use a shorter receipt image.",
                            raw=raw_text,
                        ) from e
                    if attempt == 2:
                        raise ExtractionError(
                            "The model did not return readable receipt data.", raw=raw_text
                        ) from e

            assert parsed is not None

            yield ExtractionEvent(
                stage=ExtractionProgress.VALIDATING,
                message="Checking totals",
            )
            extraction = llm.normalize_extraction(parsed, get_settings().receipt_date_order)
            extraction["validation_warnings"] = llm.validate_extraction(extraction)
            extraction["meta"] = {
                "model": self.settings.model_name,
                "prompt_version": llm.PROMPT_VERSION,
                "schema_version": llm.SCHEMA_VERSION,
                "image_count": len(images),
                "duration_seconds": round(time.monotonic() - started, 1),
            }
            if correction:
                extraction["meta"]["correction"] = correction

            yield ExtractionEvent(
                stage=ExtractionProgress.COMPLETE,
                message=f"Found {len(extraction['items'])} items",
                data={
                    "item_count": len(extraction["items"]),
                    "warning_count": len(extraction["validation_warnings"]),
                },
                result=extraction,
            )

        except ExtractionError as e:
            logger.error("Extraction failed for draft %s: %s", draft.id, e)
            yield ExtractionEvent(
                stage=ExtractionProgress.ERROR,
                message="Extraction failed",
                error=str(e),
                result={"raw": e.raw if e.raw is not None else raw_text},
            )
        except httpx.ConnectError:
            message = (
                f"Could not connect to the model server at {self.settings.model_url}. "
                "Check that it is running and that the URL is correct."
            )
            logger.error("Extraction failed for draft %s: %s", draft.id, message)
            yield ExtractionEvent(stage=ExtractionProgress.ERROR, message="Extraction failed", error=message)
        except httpx.TimeoutException:
            message = (
                f"The model did not answer within {self.timeout_seconds} seconds. "
                "It may still be loading; try again in a minute."
            )
            logger.error("Extraction failed for draft %s: %s", draft.id, message)
            yield ExtractionEvent(stage=ExtractionProgress.ERROR, message="Extraction failed", error=message)
        except Exception as e:  # noqa: BLE001 - last resort so the draft never hangs in PROCESSING
            logger.error("Extraction failed for draft %s: %s", draft.id, e, exc_info=True)
            yield ExtractionEvent(
                stage=ExtractionProgress.ERROR,
                message="Extraction failed",
                error=f"Unexpected error: {e}",
                result={"raw": raw_text},
            )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    async def save_extraction_to_draft(
        self,
        db,
        draft: ReceiptDraft,
        extracted_data: dict,
    ) -> tuple[list[ReceiptDraftItem], list[ReceiptDraftTax]]:
        """Store a normalized extraction on the draft and mark it ready for review."""
        draft.raw_llm_output = json.dumps(extracted_data, indent=2)
        draft.raw_ocr_text = None  # no separate OCR pass exists
        draft.extraction_model = self.settings.model_name
        draft.extraction_prompt_version = llm.PROMPT_VERSION
        draft.extraction_schema_version = llm.SCHEMA_VERSION

        db.query(ReceiptDraftItem).filter(ReceiptDraftItem.receipt_draft_id == draft.id).delete()
        db.query(ReceiptDraftTax).filter(ReceiptDraftTax.receipt_draft_id == draft.id).delete()

        items = []
        chain_id = corrections.chain_for_store_name(db, extracted_data.get("store_name"))
        for item_data in extracted_data.get("items", []):
            read_as = item_data["receipt_description"]
            # A fix made before to how this store's line was read is applied again.
            description = corrections.lookup(db, chain_id, read_as) or read_as
            # If a person already named this printed description, fill the common name in.
            known = receipt_records.find_confirmed_common_item(db, draft.home_id, description)
            item = ReceiptDraftItem(
                receipt_draft_id=draft.id,
                receipt_description=description,
                original_description=read_as,
                item_type=item_data.get("item_type") or "UNKNOWN",
                quantity=item_data.get("quantity"),
                weight_value=item_data.get("weight_value"),
                weight_unit=item_data.get("weight_unit"),
                unit_price=item_data.get("unit_price"),
                unit_price_unit=item_data.get("unit_price_unit"),
                line_subtotal=item_data.get("line_subtotal"),
                discount_amount=item_data.get("discount_amount"),
                line_total=item_data.get("line_total"),
                tax_amount=item_data.get("tax_amount"),
                extraction_confidence=item_data.get("extraction_confidence"),
                suggested_common_item_id=known.id if known else None,
                suggested_common_item_name=known.name if known else None,
                normalization_confidence=1.0 if known else None,
                raw_text=json.dumps(item_data),
            )
            # weight, pack size or quantity not printed: from the last time it was bought at this store
            fill_in.fill(db, draft.home_id, chain_id, item, known.id if known else None)
            db.add(item)
            items.append(item)

        taxes = []
        for tax_data in extracted_data.get("taxes", []):
            tax = ReceiptDraftTax(
                receipt_draft_id=draft.id,
                tax_type=llm.tax_type_for(tax_data.get("tax_name")),
                tax_name=tax_data.get("tax_name"),
                tax_rate=tax_data.get("tax_rate"),
                taxable_amount=tax_data.get("taxable_amount"),
                tax_amount=tax_data.get("tax_amount"),
            )
            db.add(tax)
            taxes.append(tax)

        draft.status = "NEEDS_REVIEW"
        db.commit()  # one commit: items, taxes, and status change together

        logger.info(
            "Saved extraction for draft %s: %d items, %d taxes", draft.id, len(items), len(taxes)
        )
        return items, taxes

    def keep_previous_after_failed_reread(self, db, draft: ReceiptDraft, message: str) -> None:
        """A re-read failed: keep the earlier result (and any edits) and just note why."""
        try:
            data = json.loads(draft.raw_llm_output or "{}")
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {}
        data["reextract_error"] = message
        draft.raw_llm_output = json.dumps(data, indent=2)
        draft.status = "NEEDS_REVIEW"
        db.commit()
        logger.info("Re-read failed for draft %s; kept the previous result", draft.id)

    def mark_failed(self, db, draft: ReceiptDraft, message: str, raw: str | None = None) -> None:
        """Record a failed extraction so the review page can show why and offer a retry."""
        draft.raw_llm_output = json.dumps(
            {"error": message, "raw_response": (raw or "")[:4000] or None}
        )
        draft.status = FAILED_STATUS
        db.commit()
        logger.info("Marked draft %s as %s", draft.id, FAILED_STATUS)


# Singleton instance
_extraction_service = None


def get_extraction_service() -> ExtractionService:
    """Get or create the extraction service singleton."""
    global _extraction_service
    settings = get_settings()
    if _extraction_service is None or _extraction_service.key != (settings.model_url, settings.model_timeout_seconds):
        _extraction_service = ExtractionService()   # the model server or timeout changed in App settings
    return _extraction_service
