"""Synchronous, OpenAI-compatible AI calls.

This is a deliberately smaller re-implementation of the original
``app/services/ai_service.py`` (935 lines). What was kept, and why:

  * one ``POST {AI_BASE_URL}/chat/completions`` call shape -> any
    OpenAI-compatible provider works by changing three env vars;
  * model *rotation* (the original accepted comma-separated models) so a
    free-tier daily quota can fall through to a second model;
  * graceful degradation of optional request params: providers reject
    ``logprobs`` and ``reasoning_effort`` in different ways, and the original
    already had to retry without them;
  * forgiving JSON extraction, because small models wrap JSON in prose and
    fences more often than they honour "output only JSON".

What was dropped: per-user custom endpoints from the preferences table
(multi-tenant BYO-AI is not a trial concern) and the arq-facing retry
bookkeeping. Processing is inline in the request, per the trial spec.
"""

import asyncio
import base64
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.prompts import CLOTHING_PROMPT, CLOTHING_DESCRIPTION_PROMPT

logger = logging.getLogger(__name__)


def _settings():
    """Read settings lazily: a module-level snapshot would freeze env at import
    time, which makes per-test overrides (and runtime reconfiguration) impossible."""
    return get_settings()


class AIError(RuntimeError):
    """Any failure the user should see as a clear, actionable message."""


class AINotConfiguredError(AIError):
    pass


class AIResponseError(AIError):
    pass


class AIResponseUnparsableError(AIError):
    pass


class AIResponseTruncatedError(AIError):
    pass


VALID_TYPES = {
    "shirt", "t-shirt", "top", "polo", "blouse", "tank-top", "sweater", "cardigan",
    "hoodie", "knit", "pants", "jeans", "shorts", "skirt", "dress", "jumpsuit",
    "blazer", "jacket", "coat", "vest", "shoes", "sneakers", "boots", "sandals",
    "hat", "scarf", "belt", "tie", "socks", "bag", "accessories",
}
VALID_COLORS = {
    "black", "white", "gray", "grey", "navy", "blue", "light-blue", "red", "burgundy",
    "pink", "green", "olive", "yellow", "orange", "purple", "brown", "tan", "beige",
    "cream", "gold", "silver",
}
VALID_PATTERNS = {
    "solid", "striped", "plaid", "checkered", "floral", "graphic", "geometric",
    "polka-dot", "camouflage", "animal-print",
}
VALID_MATERIALS = {
    "cotton", "denim", "leather", "wool", "polyester", "silk", "linen", "knit",
    "fleece", "suede", "velvet", "nylon", "canvas",
}
VALID_FORMALITY = {"very-casual", "casual", "smart-casual", "business-casual", "formal"}
VALID_FIT = {"slim", "regular", "relaxed", "oversized", "tailored", "cropped"}
VALID_STYLES = {
    "casual", "classic", "sporty", "minimalist", "bohemian", "preppy", "streetwear",
    "elegant", "athletic", "vintage", "modern", "rugged",
}
VALID_SEASONS = {"spring", "summer", "fall", "autumn", "winter", "all-season"}


def _clean(value: Any, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace(" ", "-")
    return v if v in allowed else None


def _clean_list(value: Any, allowed: set[str], limit: int = 4) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;|]", value)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value:
        cleaned = _clean(raw, allowed)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:limit]


class ClothingTags(BaseModel):
    """Structured clothing analysis. Field set matches the original contract."""

    type: str = "unknown"
    subtype: str | None = None
    primary_color: str | None = None
    colors: list[str] = Field(default_factory=list)
    pattern: str | None = None
    material: str | None = None
    formality: str | None = None
    style: list[str] = Field(default_factory=list)
    season: list[str] = Field(default_factory=list)
    fit: str | None = None
    occasion: list[str] = Field(default_factory=list)
    description: str | None = None
    confidence: float = 0.0
    logprobs_confidence: float | None = None
    raw_response: str | None = None
    unrecognized_type: str | None = None

    def completeness(self) -> float:
        score = 0.0
        if self.type != "unknown":
            score += 0.3
        for name, weight in (
            ("primary_color", 0.2),
            ("pattern", 0.1),
            ("material", 0.1),
            ("formality", 0.15),
            ("style", 0.05),
            ("season", 0.05),
            ("fit", 0.05),
        ):
            value = getattr(self, name)
            if value:
                score += weight
        return round(min(score, 1.0), 2)


@dataclass
class _ProviderError(Exception):
    message: str
    status_code: int | None = None


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a chatty model response."""
    if not text:
        raise AIResponseUnparsableError("The AI returned an empty response.")

    stripped = text.strip()

    def _try(candidate: str) -> Any | None:
        for prepared in (candidate, re.sub(r"//[^\n]*", "", candidate), re.sub(r"/\*[\s\S]*?\*/", "", candidate)):
            try:
                return json.loads(prepared)
            except json.JSONDecodeError:
                continue
        return None

    direct = _try(stripped)
    if direct is not None:
        return direct

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped)
    if fenced:
        inside = _try(fenced.group(1))
        if inside is not None:
            return inside

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(stripped)):
            char = stripped[i]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    found = _try(stripped[start : i + 1])
                    if found is not None:
                        return found
                    break
    raise AIResponseUnparsableError(
        "The AI response wasn't valid JSON, so the item couldn't be tagged. "
        "Try again, or edit the details manually."
    )


def confidence_from_logprobs(logprobs_content: list[dict] | None) -> float | None:
    """Mean token probability in [0,1]; None when the provider omits logprobs."""
    if not logprobs_content:
        return None
    import math

    scores = [
        top["logprob"]
        for token in logprobs_content
        if (top := (token.get("top_logprobs") or [None])[0]) and isinstance(top.get("logprob"), (int, float))
    ]
    if not scores:
        return None
    return round(math.exp(sum(scores) / len(scores)), 3)


class AIService:
    def __init__(self) -> None:
        self._base_url_override: str | None = None

    @property
    def settings(self):
        return _settings()

    @property
    def base_url(self) -> str:
        return (self._base_url_override or self.settings.ai_base_url).rstrip("/")

    @property
    def timeout(self) -> int:
        return self.settings.ai_timeout

    # -- plumbing ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_api_key}"
        return headers

    def _require_configured(self) -> None:
        if not self.base_url:
            raise AINotConfiguredError(
                "No AI provider configured on the server. Set AI_BASE_URL, AI_MODEL "
                "and AI_API_KEY (see .env.example), then restart."
            )

    @staticmethod
    def _encode_image(image_bytes: bytes) -> str:
        def _resize() -> bytes:
            from PIL import Image, ImageOps  # noqa: PLC0415

            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img = ImageOps.exif_transpose(img)
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()

        try:
            data = _resize()
        except Exception:  # noqa: BLE001 - already-validated bytes; fall back to raw
            data = image_bytes
        return base64.b64encode(data).decode("utf-8")

    def _is_rejection(self, message: str, needles: tuple[str, ...]) -> bool:
        low = message.lower()
        return any(needle in low for needle in needles)

    async def _post_once(
        self,
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, Any]],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.settings.ai_max_tokens,
            **extra,
        }
        response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str((payload.get("error") or {}).get("message") or payload)[:300]
            except Exception:  # noqa: BLE001
                detail = response.text[:300]
            raise _ProviderError(detail or response.reason_phrase, response.status_code)

        data = response.json()
        # Some gateways return 200 with {"error": ...}.
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise _ProviderError(str(err.get("message") if isinstance(err, dict) else err))
        return data

    async def _chat(
        self,
        *,
        system: str,
        user_content: Any,
        models: list[str],
        task: str,
        want_logprobs: bool = False,
    ) -> tuple[str, list[dict] | None]:
        self._require_configured()
        if not models:
            raise AINotConfiguredError("No AI model configured. Set AI_MODEL in the server environment.")

        last_error: Exception | None = None
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for model in models:
                use_logprobs = want_logprobs
                use_reasoning = bool(self.settings.ai_reasoning_effort)
                attempt = 0
                # Negotiating away unsupported optional params must not consume
                # the retry budget: an endpoint that rejects logprobs is not a
                # flaky endpoint, and with AI_MAX_RETRIES=1 a counted rejection
                # would abandon the model before it ever got a real answer.
                while attempt < self.settings.ai_max_retries:
                    extra: dict[str, Any] = {}
                    if use_logprobs:
                        extra["logprobs"] = True
                        extra["top_logprobs"] = 3
                    if use_reasoning and self.settings.ai_reasoning_effort:
                        extra["reasoning_effort"] = self.settings.ai_reasoning_effort
                    try:
                        data = await self._post_once(client, model, messages, extra)
                    except httpx.TimeoutException:
                        last_error = AIResponseError(
                            f"The AI provider took longer than {self.timeout}s to answer. Try again."
                        )
                        break  # slow provider: switch model rather than retry it
                    except httpx.HTTPStatusError as exc:  # pragma: no cover - raised by raise_for_status
                        last_error = _ProviderError(str(exc), exc.response.status_code)
                    except _ProviderError as exc:
                        # Optional request params are negotiated, not assumed. A
                        # gateway that dislikes logprobs/reasoning_effort says so in
                        # a 4xx, and the only useful response is to drop the param
                        # and try again — matching on the *named* param is too
                        # narrow, since many gateways report a generic
                        # "unsupported parameter" without naming it.
                        optional_rejection = exc.status_code in (400, 404, 422) and (
                            exc.message.lower().startswith("unsupported parameter")
                            or any(
                                needle in exc.message.lower()
                                for needle in ("logprobs", "logprob", "reasoning_effort", "reasoning")
                            )
                        )
                        if optional_rejection and use_logprobs:
                            logger.info("%s: provider rejected optional params, dropping logprobs", task)
                            use_logprobs = False
                            continue  # not counted as a retry
                        if optional_rejection and use_reasoning:
                            logger.info("%s: provider rejected optional params, dropping reasoning_effort", task)
                            use_reasoning = False
                            continue  # not counted as a retry
                        if exc.status_code == 401:
                            raise AIResponseError(
                                "The AI provider rejected the API key (401). Check AI_API_KEY on the server."
                            ) from None
                        if exc.status_code == 403:
                            raise AIResponseError(
                                "The AI provider refused this request (403). The key may not grant "
                                f"access to model '{model}'."
                            ) from None
                        if exc.status_code == 404:
                            last_error = AIResponseError(
                                f"The AI provider has no model '{model}' at {self.base_url}. "
                                "Check AI_MODEL / AI_BASE_URL."
                            )
                            break  # wrong model: try the next configured one
                        if exc.status_code == 429:
                            last_error = AIResponseError(
                                "The AI free-tier quota is exhausted right now (429). "
                                "Wait a minute, or set AI_MODEL to another model."
                            )
                            break
                        if exc.status_code in (500, 502, 503, 504):
                            last_error = AIResponseError(
                                f"The AI provider is unavailable ({exc.status_code}). Try again shortly."
                            )
                        else:
                            last_error = AIResponseError(f"AI provider error: {exc.message}")

                    else:
                        choice = (data.get("choices") or [{}])[0]
                        message = choice.get("message") or {}
                        content = message.get("content")
                        if isinstance(content, list):  # some gateways return content parts
                            content = "".join(
                                part.get("text", "") for part in content if isinstance(part, dict)
                            )
                        if not content:
                            content = message.get("reasoning_content") or ""
                        if choice.get("finish_reason") == "length":
                            last_error = AIResponseTruncatedError(
                                "The AI response was cut off before it finished. "
                                "Raise AI_MAX_TOKENS or use a model with a longer output budget."
                            )
                            break
                        return (content or "").strip(), (message.get("logprobs") or {}).get("content")

                    # Only reached after a real failure: counted and backed off.
                    attempt += 1
                    if attempt < self.settings.ai_max_retries:
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
                # next model

        if isinstance(last_error, AIError):
            raise last_error
        raise AIResponseError(str(last_error or "The AI provider did not return a response."))

    # -- public API ----------------------------------------------------------
    async def analyze_image_bytes(self, image_bytes: bytes) -> ClothingTags:
        """One image in, structured clothing tags out. Two calls: tags, then caption."""
        b64 = await asyncio.to_thread(self._encode_image, image_bytes)
        user_content = [
            {"type": "text", "text": "Analyze the main garment in this photo."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]

        raw, logprobs = await self._chat(
            system=CLOTHING_PROMPT,
            user_content=user_content,
            models=self.settings.ai_vision_models,
            task="tagging",
            want_logprobs=True,
        )

        payload = extract_json(raw)
        if isinstance(payload, list):
            payload = next((entry for entry in payload if isinstance(entry, dict)), {})
        if not isinstance(payload, dict):
            payload = {}

        tags = ClothingTags(raw_response=raw[:2000])
        reported_type = (payload.get("type") or "").strip().lower()
        clean_type = _clean(reported_type, VALID_TYPES)
        if clean_type:
            tags.type = clean_type
        elif reported_type and reported_type != "unknown":
            tags.type = "unknown"
            tags.unrecognized_type = reported_type[:100]

        subtype = payload.get("subtype")
        tags.subtype = subtype.strip()[:50] if isinstance(subtype, str) and subtype.strip() else None
        tags.primary_color = _clean(payload.get("primary_color"), VALID_COLORS)
        tags.colors = _clean_list(payload.get("colors"), VALID_COLORS, limit=5)
        if tags.primary_color and tags.primary_color not in tags.colors:
            tags.colors.insert(0, tags.primary_color)
        tags.pattern = _clean(payload.get("pattern"), VALID_PATTERNS)
        tags.material = _clean(payload.get("material"), VALID_MATERIALS)
        tags.formality = _clean(payload.get("formality"), VALID_FORMALITY)
        tags.fit = _clean(payload.get("fit"), VALID_FIT)
        tags.style = _clean_list(payload.get("style"), VALID_STYLES)
        tags.season = _clean_list(payload.get("season"), VALID_SEASONS)
        tags.logprobs_confidence = confidence_from_logprobs(logprobs)
        tags.confidence = tags.completeness()

        # A caption is a nice-to-have: never fail the upload over it.
        try:
            description, _ = await self._chat(
                system=CLOTHING_DESCRIPTION_PROMPT,
                user_content=user_content,
                models=self.settings.ai_vision_models,
                task="description",
            )
            tags.description = description.strip().strip('"')[:500] or None
        except AIError as exc:
            logger.info("Description pass skipped: %s", exc)
            tags.description = None

        return tags

    async def analyze_image(self, image_path: str | Path) -> ClothingTags:
        return await self.analyze_image_bytes(Path(image_path).read_bytes())

    async def generate_text(self, *, system: str, user: str) -> str:
        content, _ = await self._chat(
            system=system, user_content=user, models=self.settings.ai_text_models, task="text"
        )
        return content

    async def check_health(self) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "not-configured", "detail": "AI_BASE_URL is empty"}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
            if response.status_code < 400:
                return {"status": "ok", "detail": f"{self.base_url} reachable ({response.status_code})"}
            return {"status": "degraded", "detail": f"{self.base_url} returned {response.status_code}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unreachable", "detail": f"{self.base_url}: {exc}"}


_service: AIService | None = None


def get_ai() -> AIService:
    global _service
    if _service is None:
        _service = AIService()
    return _service


__all__ = [
    "AIError",
    "AINotConfiguredError",
    "AIResponseError",
    "AIResponseTruncatedError",
    "AIResponseUnparsableError",
    "ClothingTags",
    "ValidationError",
    "extract_json",
    "get_ai",
]
