"""Ticket drafting in English, Hindi or Punjabi.

Gemini writes the draft from the validated photo analysis (never from the raw
photo, which is not sent to the drafting model). Output is schema-validated;
on failure we retry once and then fall back to fixed templates, so a ticket
always exists for the official to review. Nothing is dispatched without approval.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from pydantic import ValidationError

from .config import DraftingConfig
from .schemas import Lang, TicketDraft

log = logging.getLogger(__name__)

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari script)", "pa": "Punjabi (Gurmukhi script)"}

KIND = {
    "en": {"overflowing_bin": "Overflowing bin", "illegal_dump": "Illegal dumping", "litter": "Scattered litter",
           "clean": "No waste visible"},
    "hi": {"overflowing_bin": "भरा हुआ कूड़ादान", "illegal_dump": "अवैध कचरा ढेर", "litter": "बिखरा हुआ कचरा",
           "clean": "कोई कचरा नहीं"},
    "pa": {"overflowing_bin": "ਭਰਿਆ ਹੋਇਆ ਕੂੜੇਦਾਨ", "illegal_dump": "ਗੈਰ-ਕਾਨੂੰਨੀ ਕੂੜੇ ਦਾ ਢੇਰ", "litter": "ਖਿੱਲਰਿਆ ਕੂੜਾ",
           "clean": "ਕੋਈ ਕੂੜਾ ਨਹੀਂ"},
}
WASTE = {
    "en": {"mixed": "mixed waste", "dry_recyclable": "dry / recyclable waste", "wet_organic": "wet / organic waste",
           "construction_debris": "construction debris", "bulky": "bulky items", "e_waste": "e-waste",
           "hazardous": "hazardous waste", "none": "none"},
    "hi": {"mixed": "मिश्रित कचरा", "dry_recyclable": "सूखा / पुनर्चक्रण योग्य कचरा", "wet_organic": "गीला / जैविक कचरा",
           "construction_debris": "निर्माण मलबा", "bulky": "भारी सामान", "e_waste": "ई-कचरा",
           "hazardous": "खतरनाक कचरा", "none": "कोई नहीं"},
    "pa": {"mixed": "ਮਿਲਿਆ-ਜੁਲਿਆ ਕੂੜਾ", "dry_recyclable": "ਸੁੱਕਾ / ਰੀਸਾਈਕਲ ਹੋਣ ਯੋਗ ਕੂੜਾ", "wet_organic": "ਗਿੱਲਾ / ਜੈਵਿਕ ਕੂੜਾ",
           "construction_debris": "ਉਸਾਰੀ ਦਾ ਮਲਬਾ", "bulky": "ਵੱਡਾ ਸਮਾਨ", "e_waste": "ਈ-ਕੂੜਾ",
           "hazardous": "ਖ਼ਤਰਨਾਕ ਕੂੜਾ", "none": "ਕੋਈ ਨਹੀਂ"},
}
TEMPLATE = {
    "en": ("{kind} in Sector {sector}",
           "Citizen report from Sector {sector}: {kind_l} ({waste}), severity {severity}/5. "
           "Observed: {reason} Please schedule collection."),
    "hi": ("सेक्टर {sector}: {kind}",
           "सेक्टर {sector} से नागरिक रिपोर्ट: {kind} ({waste}), गंभीरता {severity}/5। "
           "कृपया कचरा उठाने की व्यवस्था करें।"),
    "pa": ("ਸੈਕਟਰ {sector}: {kind}",
           "ਸੈਕਟਰ {sector} ਤੋਂ ਨਾਗਰਿਕ ਰਿਪੋਰਟ: {kind} ({waste}), ਗੰਭੀਰਤਾ {severity}/5। "
           "ਕਿਰਪਾ ਕਰਕੇ ਕੂੜਾ ਚੁੱਕਣ ਦਾ ਪ੍ਰਬੰਧ ਕਰੋ।"),
}


class Drafter(Protocol):
    name: str

    def draft(self, lang: Lang, facts: dict) -> TicketDraft: ...


def template_draft(lang: Lang, facts: dict) -> TicketDraft:
    kind = KIND[lang].get(facts.get("kind") or "", "Report needing review")
    title_t, body_t = TEMPLATE[lang]
    return TicketDraft(
        title=title_t.format(kind=kind, sector=facts["sector"])[:120],
        body=body_t.format(kind=kind, kind_l=kind.lower(), sector=facts["sector"],
                           waste=WASTE[lang].get(facts.get("waste_type") or "", "?"),
                           severity=facts.get("severity") or "?",
                           reason=(facts.get("reason") or "").rstrip(".") + ".")[:1200],
    )


class TemplateDrafter:
    name = "template"

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        return template_draft(lang, facts)


DRAFT_PROMPT = """You draft work tickets for a municipal solid-waste team in Chandigarh, India.
Write the ticket in {lang_name}. Use only the facts below; do not invent addresses, names, dates or numbers.
Facts (JSON): {facts}
Return JSON with:
- "title": at most 10 words, naming the problem and the sector
- "body": 2-4 short sentences for the field supervisor: what was reported, where (sector), how severe,
  what kind of vehicle/crew is likely needed (e.g. a tipper for construction debris, a regular collection
  vehicle for household waste, a separate pickup for e-waste or hazardous waste).
  Do not mention a score or priority number: the live score is shown separately and changes over time.
Keep it plain and polite. This is a DRAFT that a human official will review before anything is dispatched."""


class GeminiDrafter:
    """`models` is an ordered fallback chain: free-tier quotas are per model per day,
    so if one model is exhausted (429) or overloaded (5xx) the next one is tried."""

    def __init__(self, models: list[str], api_key: str, timeout_s: float) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self.models = models
        self.name = f"gemini-api:{models[0]}"
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def _call(self, model: str, prompt: str) -> TicketDraft:
        resp = self.client.models.generate_content(
            model=model, contents=prompt,
            config=self._types.GenerateContentConfig(
                temperature=0.2, response_mime_type="application/json",
                response_json_schema=TicketDraft.model_json_schema()),
        )
        return TicketDraft.model_validate_json(resp.text or "")

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        from google.genai import errors

        base = DRAFT_PROMPT.format(lang_name=LANG_NAMES[lang], facts=json.dumps(facts, ensure_ascii=False))
        last: Exception | None = None
        for model in self.models:
            prompt = base
            for _ in range(2):  # one retry per model for invalid output
                try:
                    d = self._call(model, prompt)
                    self.name = f"gemini-api:{model}"
                    return d
                except (ValidationError, ValueError) as exc:
                    last = exc
                    prompt = base + f"\nYour previous answer was invalid ({str(exc)[:200]}). Return only valid JSON."
                except errors.ClientError as exc:
                    last = exc
                    if getattr(exc, "code", None) != 429:
                        raise
                    break  # quota exhausted: next model
                except Exception as exc:  # 5xx, timeouts
                    last = exc
                    break
        raise RuntimeError(f"drafting failed: {last}")


class GroqDrafter:
    """Last-resort model (Groq free tier) used when every Gemini model has failed."""

    def __init__(self, model: str, api_key: str, timeout_s: float) -> None:
        from .groq_client import GroqClient

        self.model = model
        self.name = f"groq:{model}"
        self.client = GroqClient(api_key, timeout_s)

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        base = DRAFT_PROMPT.format(lang_name=LANG_NAMES[lang], facts=json.dumps(facts, ensure_ascii=False))
        prompt = base + f"\nReturn only JSON matching this schema: {json.dumps(TicketDraft.model_json_schema())}"
        last: Exception | None = None
        for _ in range(2):  # one retry for invalid output
            try:
                return TicketDraft.model_validate_json(
                    self.client.chat(self.model, [{"role": "user", "content": prompt}],
                                     temperature=0.2, json_mode=True))
            except (ValidationError, ValueError) as exc:
                last = exc
                prompt = base + f"\nYour previous answer was invalid ({str(exc)[:200]}). Return only valid JSON."
        raise RuntimeError(f"groq drafting failed: {last}")


class FallbackDrafter:
    """Try Gemini, then Groq if configured; on any failure use the template so a ticket always exists."""

    def __init__(self, primary: Drafter | None, secondary: Drafter | None = None) -> None:
        self.primary = primary
        self.secondary = secondary
        self.last_used = "template"

    @property
    def name(self) -> str:
        first = self.primary or self.secondary
        return first.name if first else "template"

    def draft(self, lang: Lang, facts: dict) -> TicketDraft:
        for drafter in (self.primary, self.secondary):
            if drafter is None:
                continue
            try:
                d = drafter.draft(lang, facts)
                self.last_used = drafter.name
                return d
            except Exception as exc:
                log.warning('Drafting with %s failed, trying next option: %s', drafter.name, exc)
        self.last_used = "template"
        return template_draft(lang, facts)


def build_drafter(cfg: DraftingConfig, api_key: str | None, groq_api_key: str | None = None) -> FallbackDrafter:
    if cfg.provider != "gemini":
        return FallbackDrafter(None)
    primary = GeminiDrafter(cfg.models, api_key, cfg.timeout_s) if api_key else None
    secondary = GroqDrafter(cfg.groq_model, groq_api_key, cfg.timeout_s) if groq_api_key and cfg.groq_model else None
    return FallbackDrafter(primary, secondary)
